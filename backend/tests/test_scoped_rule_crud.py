"""Characterisation tests for the group- and host-scoped rule CRUD endpoints.

Eight modules expose near-identical create, update and delete endpoints for
a group and for a host: services, Linux users and groups, cron jobs, hosts
entries, packages (and package repositories), CA certificates and firewall
rules. Their tests covered 31 to 56% of them. These drive every one through
the same script and pin, against the current code, what each returns and
what it writes to the audit log, the 404s for a missing scope or rule (and
for a rule asked for through the wrong scope), the GitOps lock on the group
side, and the 409 on a duplicate, so moving their shared body into helpers
has something to answer to.

Where a module differs, the table says so: firewall rules are not audited
and their group create checks neither the group nor a duplicate; CA
certificates take no GitOps lock; package repositories exist only on the
group side; cron jobs catch a duplicate their table does not forbid, so
two identical jobs are both stored.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from sqlalchemy import select

from app.models.audit_log import AuditLog
from tests.conftest import create_group, create_host

pytestmark = pytest.mark.integration


def _pem(common_name: str) -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


_PEM = _pem("Scoped CRUD CA")


def _fields(*names: str) -> Callable[[dict], dict]:
    return lambda row: {n: row[n] for n in names}


@dataclass
class Kind:
    name: str
    group_path: str | None
    host_path: str | None
    create: dict
    update: dict
    not_found: str
    #: None: the module writes no audit rows.
    entity_type: str | None = None
    audit: Callable[[dict], dict] | None = None
    delete_audit: Callable[[dict], dict] | None = None
    locked: bool = True
    #: Create-conflict detail, given the scope words; None: duplicates are allowed.
    conflict: Callable[[str], str] | None = None
    #: The group create 404s a missing group.
    group_create_checks_group: bool = True
    #: The group delete answers 204 with a body-less Response (not the decorator's code).
    delete_status: int = 204


KINDS = [
    Kind(
        "services",
        "services",
        "services",
        {"service_name": "nginx", "state": "running"},
        {"state": "stopped", "comment": None},
        "Service rule not found",
        "service_rule",
        lambda r: {"service_name": r["service_name"], "state": r["state"]},
    ),
    Kind(
        "linux_users",
        "linux-users",
        "linux-users",
        {"username": "alice", "uid": 1500},
        {"state": "absent", "comment": None},
        "Linux user rule not found",
        "linux_user",
        lambda r: {"username": r["username"], "state": r["state"]},
    ),
    Kind(
        "linux_groups",
        "linux-groups",
        "linux-groups",
        {"groupname": "ops", "gid": 1600},
        {"state": "absent"},
        "Linux group rule not found",
        "linux_group",
        lambda r: {"groupname": r["groupname"], "state": r["state"]},
    ),
    Kind(
        "cron_jobs",
        "cron-jobs",
        "cron-jobs",
        {"name": "backup", "user": "root", "schedule": "0 1 * * *", "command": "/bin/true"},
        {"schedule": "0 2 * * *", "comment": None},
        "Cron job rule not found",
        "cron_job",
        _fields("name", "user", "state"),
        # The endpoints turn an IntegrityError into a 409, but cron_jobs has no
        # unique constraint, so a duplicate is stored.
        conflict=None,
    ),
    Kind(
        "hosts_entries",
        "hosts-entries",
        "hosts-entries",
        {"ip_address": "10.1.2.3", "hostname": "db.internal"},
        {"hostname": "db2.internal"},
        "Hosts entry not found",
        "hosts_entry",
        _fields("ip_address", "hostname", "host_ref_id"),
        delete_audit=_fields("ip_address", "hostname"),
    ),
    Kind(
        "packages",
        "packages",
        "packages",
        {"package_name": "curl", "version": "8.0"},
        {"state": "latest", "comment": None},
        "Package rule not found",
        "package_rule",
        _fields("package_name", "state", "version"),
        conflict=lambda where: f"Package 'curl' already exists {where}",
    ),
    Kind(
        "package_repos",
        "package-repos",
        None,
        {"name": "vendor", "url": "https://repo.example/apt", "repo_type": "apt"},
        {"state": "absent"},
        "Package repository not found",
        "package_repo",
        _fields("name", "url", "state"),
        conflict=lambda where: f"Repository 'vendor' already exists {where}",
    ),
    Kind(
        "ca_certs",
        "ca-certs",
        "ca-certs",
        {"name": "internal-ca", "pem_content": _PEM},
        {"name": "renamed-ca", "state": "absent"},
        "CA certificate rule not found",
        "ca_cert_rule",
        _fields("name", "fingerprint_sha256", "state"),
        locked=False,
        conflict=None,  # set below: the detail names the fingerprint
    ),
    Kind(
        "firewall_rules",
        "rules",
        "firewall-rules",
        {"action": "allow", "protocol": "tcp", "direction": "input", "port_start": 22},
        {"port_start": 2222, "comment": None},
        "Rule not found",
        group_create_checks_group=False,
    ),
]


def _ca_conflict(where: str) -> str:
    from app.ca_certs.pem_utils import parse_pem_certificate

    fp = parse_pem_certificate(_PEM).fingerprint_sha256
    return f"CA certificate with fingerprint {fp} already exists {where}"


KINDS[7].conflict = _ca_conflict

CASES = [(k, "group") for k in KINDS if k.group_path] + [(k, "host") for k in KINDS if k.host_path]
IDS = [f"{k.name}-{scope}" for k, scope in CASES]


@dataclass
class Scope:
    kind: Kind
    scope: str
    owner_id: int
    other_id: int
    audit_since: int = 0
    seen: list = field(default_factory=list)

    @property
    def base(self) -> str:
        if self.scope == "group":
            return f"/api/groups/{self.owner_id}/{self.kind.group_path}"
        return f"/api/hosts/{self.owner_id}/{self.kind.host_path}"

    @property
    def other_base(self) -> str:
        if self.scope == "group":
            return f"/api/groups/{self.other_id}/{self.kind.group_path}"
        return f"/api/hosts/{self.other_id}/{self.kind.host_path}"

    @property
    def where(self) -> str:
        return "in this group" if self.scope == "group" else "on this host"


async def _scope(db, kind: Kind, scope: str) -> Scope:
    if scope == "group":
        owner, other = await create_group(db), await create_group(db)
    else:
        owner, other = await create_host(db), await create_host(db, ip="10.0.0.2")
    await db.commit()
    last = await db.scalar(select(AuditLog.id).order_by(AuditLog.id.desc()).limit(1))
    return Scope(kind, scope, owner.id, other.id, audit_since=last or 0)


async def _audit(db, s: Scope) -> list[tuple]:
    rows = (
        (
            await db.execute(
                select(AuditLog).where(AuditLog.id > s.audit_since).order_by(AuditLog.id)
            )
        )
        .scalars()
        .all()
    )
    return [(r.action, r.entity_type, r.entity_id, r.before_state, r.after_state) for r in rows]


@pytest.mark.parametrize(("kind", "scope"), CASES, ids=IDS)
async def test_create_update_delete_round_trip(superuser_client, db, kind, scope):
    s = await _scope(db, kind, scope)

    created = await superuser_client.post(s.base, json=kind.create)
    assert created.status_code == 201, created.text
    row = created.json()
    assert row[f"{scope}_id"] == s.owner_id
    for key, value in kind.create.items():
        if key != "pem_content":
            assert row[key] == value

    updated = await superuser_client.put(f"{s.base}/{row['id']}", json=kind.update)
    assert updated.status_code == 200, updated.text
    after = updated.json()
    for key, value in kind.update.items():
        if value is not None:
            assert after[key] == value
    assert after["id"] == row["id"]

    listed = await superuser_client.get(s.base)
    assert [r["id"] for r in listed.json()] == [row["id"]]

    deleted = await superuser_client.delete(f"{s.base}/{row['id']}")
    assert deleted.status_code == kind.delete_status
    assert deleted.content == b""
    assert (await superuser_client.get(s.base)).json() == []

    audit = await _audit(db, s)
    if kind.entity_type is None:
        assert audit == []
        return
    delete_state = (kind.delete_audit or kind.audit)(after)
    assert audit == [
        ("create", kind.entity_type, row["id"], None, kind.audit(row)),
        ("update", kind.entity_type, row["id"], kind.audit(row), kind.audit(after)),
        ("delete", kind.entity_type, row["id"], delete_state, None),
    ]


@pytest.mark.parametrize(("kind", "scope"), CASES, ids=IDS)
async def test_a_missing_or_foreign_rule_is_404(superuser_client, db, kind, scope):
    s = await _scope(db, kind, scope)
    row = (await superuser_client.post(s.base, json=kind.create)).json()

    for method, url, body in [
        ("put", f"{s.base}/987654", kind.update),
        ("delete", f"{s.base}/987654", None),
        ("put", f"{s.other_base}/{row['id']}", kind.update),
        ("delete", f"{s.other_base}/{row['id']}", None),
    ]:
        kwargs = {"json": body} if body is not None else {}
        resp = await getattr(superuser_client, method)(url, **kwargs)
        assert (resp.status_code, resp.json()) == (404, {"detail": kind.not_found}), url

    assert len(await _audit(db, s)) == (1 if kind.entity_type else 0)


@pytest.mark.parametrize(("kind", "scope"), CASES, ids=IDS)
async def test_create_in_a_missing_scope(superuser_client, kind, scope):
    path = kind.group_path if scope == "group" else kind.host_path
    url = f"/api/{scope}s/987654/{path}"
    if scope == "group" and not kind.group_create_checks_group:
        pytest.skip("firewall group create does not look the group up")
    resp = await superuser_client.post(url, json=kind.create)
    detail = "Group not found" if scope == "group" else "Host not found"
    assert (resp.status_code, resp.json()) == (404, {"detail": detail})


@pytest.mark.parametrize(("kind", "scope"), CASES, ids=IDS)
async def test_a_duplicate_create(superuser_client, db, kind, scope):
    s = await _scope(db, kind, scope)
    first = await superuser_client.post(s.base, json=kind.create)
    assert first.status_code == 201
    second = await superuser_client.post(s.base, json=kind.create)
    if kind.conflict is None:
        assert second.status_code == 201
        return
    assert (second.status_code, second.json()) == (409, {"detail": kind.conflict(s.where)})
    assert len(await _audit(db, s)) == 1
    assert [r["id"] for r in (await superuser_client.get(s.base)).json()] == [first.json()["id"]]


@pytest.mark.parametrize("kind", [k for k in KINDS if k.group_path], ids=lambda k: k.name)
async def test_the_gitops_lock_on_group_writes(superuser_client, db, kind):
    s = await _scope(db, kind, "group")
    row = (await superuser_client.post(s.base, json=kind.create)).json()
    from app.models.host_group import HostGroup

    group = await db.get(HostGroup, s.owner_id)
    group.gitops_enabled = True
    await db.commit()

    second = dict(kind.create)
    responses = [
        await superuser_client.post(s.base, json=second),
        await superuser_client.put(f"{s.base}/{row['id']}", json=kind.update),
        await superuser_client.delete(f"{s.base}/{row['id']}"),
    ]
    if kind.locked:
        message = "This group is managed by GitOps. Changes must be made via Git."
        assert [(r.status_code, r.json()) for r in responses] == [(403, {"detail": message})] * 3
    else:
        assert [r.status_code for r in responses][1:] == [200, 204]


async def test_a_host_rule_update_and_delete_do_not_look_the_host_up(superuser_client, db):
    """The host-side update and delete only scope the rule query by host id,
    so a missing host is reported as a missing rule."""
    resp = await superuser_client.put("/api/hosts/987654/services/1", json={"state": "stopped"})
    assert (resp.status_code, resp.json()) == (404, {"detail": "Service rule not found"})
