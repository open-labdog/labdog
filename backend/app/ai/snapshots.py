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
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.settings_service import get_setting_typed

logger = logging.getLogger(__name__)


class SnapshotFailed(Exception):
    """A snapshot was expected for this host and could not be taken."""


async def snapshot_if_mutating(
    db: AsyncSession,
    *,
    classification: str,
    arguments: dict,
    session_id: int,
    label: str = "",
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
            await snapshot_before_change(db, host_id=host_id, session_id=session_id, label=label),
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
) -> str | None:
    """Snapshot ``host_id``'s VM, or ``None`` if it has no VM to snapshot.

    Raises :class:`SnapshotFailed` when the host *does* map to a VM and
    the snapshot could not be created.
    """
    if not int(await get_setting_typed("ai.snapshot_before_mutating", db)):
        return None

    from app.crypto.encryption import decrypt_ssh_key
    from app.crypto.key_management import get_master_key
    from app.proxmox.client import ProxmoxClient
    from app.proxmox.models import ProxmoxNode
    from app.proxmox.vm_mapping import VMMapping
    from app.workflows.steps.snapshot import create_snapshot

    mapping = (
        await db.execute(select(VMMapping).where(VMMapping.host_id == host_id))
    ).scalar_one_or_none()
    if mapping is None:
        logger.info(
            "ai session %s: host %s has no VM mapping; no snapshot taken",
            session_id,
            host_id,
        )
        return None

    node = (
        await db.execute(select(ProxmoxNode).where(ProxmoxNode.id == mapping.proxmox_node_id))
    ).scalar_one_or_none()
    if node is None:
        raise SnapshotFailed(
            f"Host {host_id} maps to VM {mapping.vmid}, but its Proxmox node is no "
            f"longer configured in LabDog, so no snapshot could be taken. The "
            f"command was not run."
        )

    try:
        token_secret = decrypt_ssh_key(node.encrypted_token_secret, get_master_key())
        client = ProxmoxClient(
            api_url=node.api_url,
            token_id=node.token_id,
            token_secret=token_secret,
            verify_ssl=node.verify_ssl,
            ca_cert_pem=node.ca_cert_pem,
        )
        return await create_snapshot(
            client,
            mapping.pve_node_name,
            mapping.vmid,
            session_id,
            mapping.vm_type,
            action_key=f"ai:{label[:80]}" if label else "ai",
        )
    except Exception as exc:
        logger.warning(
            "ai session %s: snapshot of vmid %s failed: %s", session_id, mapping.vmid, exc
        )
        raise SnapshotFailed(
            f"Could not snapshot {mapping.vm_type} {mapping.vmid} before making this "
            f"change ({exc}). The command was not run — without a rollback point "
            f"this is a riskier change than the one that was approved. Fix the "
            f"Proxmox connection, or re-approve knowing there is no snapshot."
        ) from exc
