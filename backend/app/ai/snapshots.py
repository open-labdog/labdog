"""A rollback point before the agent changes a host.

Reuses the same Proxmox machinery a destructive action pack gets
(:func:`app.workflows.steps.snapshot.create_snapshot`), for the same
reason: a change made by an agent is not more trustworthy than one made
by a playbook, and an operator should not have to know which made it in
order to undo it.

Two behaviours are deliberate and easy to get backwards.

**A host with no VM mapping proceeds without a snapshot.** Bare metal and
unmapped containers are ordinary in a homelab, and refusing to administer
them would make the whole autonomy level useless on exactly the hosts
most likely to need attention. Returning ``None`` says "there is no
rollback point", and the caller records that.

**A snapshot that *fails* blocks the command.** This is the opposite
decision, and the distinction is between a host that never had a safety
net and one whose net was expected and did not appear. The second is a
Proxmox problem — an unreachable node, a bad token, a full datastore —
and running a change into it converts an approval the operator gave under
one set of assumptions into a riskier change than the one they agreed to.

Snapshots taken here are named ``labdog-ai-<session>-<ts>`` rather than
sharing the action-run prefix. The two have different lifetimes — an
action deletes its own snapshot as soon as its verify step passes, while
an agent's is kept for a retention window precisely so a human can undo
the change later — and an operator deciding what is safe to remove by
hand needs to be able to tell them apart.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.settings_service import get_setting_typed

logger = logging.getLogger(__name__)

#: Distinguishes an agent's snapshot from an action run's. Also what the
#: retention sweep matches on, so a change to it strands existing
#: snapshots rather than deleting the wrong ones.
AI_SNAPSHOT_PREFIX = "labdog-ai"


class SnapshotFailed(Exception):
    """A snapshot was expected for this host and could not be taken."""


@dataclass(frozen=True)
class SnapshotTarget:
    """Everything needed to snapshot or unsnapshot one host's VM."""

    client: Any
    pve_node: str
    vmid: int
    vm_type: str


async def resolve_target(db: AsyncSession, host_id: int) -> SnapshotTarget | None:
    """The Proxmox handle for ``host_id``, or ``None`` if it has no VM.

    Shared by the creating path and the retention sweep so they cannot
    disagree about which VM a host is — a sweep that resolved hosts
    differently from the code that took the snapshots would delete the
    wrong ones, or nothing.
    """
    from app.crypto.encryption import decrypt_ssh_key
    from app.crypto.key_management import get_master_key
    from app.proxmox.client import ProxmoxClient
    from app.proxmox.models import ProxmoxNode
    from app.proxmox.vm_mapping import VMMapping

    mapping = (
        await db.execute(select(VMMapping).where(VMMapping.host_id == host_id))
    ).scalar_one_or_none()
    if mapping is None:
        return None

    node = (
        await db.execute(select(ProxmoxNode).where(ProxmoxNode.id == mapping.proxmox_node_id))
    ).scalar_one_or_none()
    if node is None:
        raise SnapshotFailed(
            f"Host {host_id} maps to VM {mapping.vmid}, but its Proxmox node is no "
            f"longer configured in LabDog."
        )

    token_secret = decrypt_ssh_key(node.encrypted_token_secret, get_master_key())
    return SnapshotTarget(
        client=ProxmoxClient(
            api_url=node.api_url,
            token_id=node.token_id,
            token_secret=token_secret,
            verify_ssl=node.verify_ssl,
            ca_cert_pem=node.ca_cert_pem,
        ),
        pve_node=mapping.pve_node_name,
        vmid=mapping.vmid,
        vm_type=mapping.vm_type,
    )


async def snapshots_enabled(db: AsyncSession, *, skip: bool) -> bool:
    """Whether this session should snapshot before a change.

    The instance setting and the session's own opt-out compose by
    agreement rather than override: both must want a snapshot for one to
    be taken. That direction is deliberate — a session cannot re-enable
    snapshots an operator turned off instance-wide.
    """
    if skip:
        return False
    return bool(int(await get_setting_typed("ai.snapshot_before_mutating", db)))


async def snapshot_if_mutating(
    db: AsyncSession,
    *,
    classification: str,
    arguments: dict,
    session_id: int,
    label: str = "",
    skip: bool = False,
) -> tuple[str | None, str | None]:
    """Snapshot before a ``full_auto`` write, if this call is one.

    Returns ``(snapshot_name, refusal)``. A non-empty ``refusal`` means
    the caller must not run the command and should hand that text back to
    the model instead.

    Separate from :func:`snapshot_before_change` so the in-line path can
    stay a two-line call in the middle of a tool runner, and so the "is
    this even a write?" question is answered in one place rather than
    once per runner.
    """
    if classification != "mutating":
        return None, None
    host_id = arguments.get("host_id")
    if not isinstance(host_id, int):
        return None, None
    try:
        return (
            await snapshot_before_change(
                db, host_id=host_id, session_id=session_id, label=label, skip=skip
            ),
            None,
        )
    except SnapshotFailed as exc:
        return None, str(exc)


async def snapshot_before_change(
    db: AsyncSession,
    *,
    host_id: int,
    session_id: int,
    label: str = "",
    skip: bool = False,
) -> str | None:
    """Snapshot ``host_id``'s VM, or ``None`` if it has no VM to snapshot.

    Raises :class:`SnapshotFailed` when the host *does* map to a VM and
    the snapshot could not be created.
    """
    if not await snapshots_enabled(db, skip=skip):
        return None

    from app.workflows.steps.snapshot import create_snapshot

    target = await resolve_target(db, host_id)
    if target is None:
        logger.info(
            "ai session %s: host %s has no VM mapping; no snapshot taken",
            session_id,
            host_id,
        )
        return None

    try:
        return await create_snapshot(
            target.client,
            target.pve_node,
            target.vmid,
            session_id,
            target.vm_type,
            action_key=f"ai:{label[:80]}" if label else "ai",
            name_prefix=AI_SNAPSHOT_PREFIX,
        )
    except Exception as exc:
        logger.warning(
            "ai session %s: snapshot of vmid %s failed: %s", session_id, target.vmid, exc
        )
        raise SnapshotFailed(
            f"Could not snapshot {target.vm_type} {target.vmid} before making this "
            f"change ({exc}). The command was not run — without a rollback point "
            f"this is a riskier change than the one that was approved. Fix the "
            f"Proxmox connection, or re-approve knowing there is no snapshot."
        ) from exc
