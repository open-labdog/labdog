"""Tests for the git_repos API and the URL-based auth-type detection.

The API derives ``auth_type`` from the URL scheme + supplied
credentials — the form does not collect it. SSH URLs require an SSH
key; HTTPS URLs use the token if present and fall through to public
(``auth_type=none``) otherwise. These tests exercise each branch
including the rejection paths.
"""

from __future__ import annotations

import pytest

from app.schemas.git_repos import GitRepoCreate, derive_auth_type

# ---------------------------------------------------------------------------
# Pure-function unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "ssh_key_id", "https_token", "expected"),
    [
        ("https://github.com/foo/bar", None, None, "none"),
        ("https://github.com/foo/bar", None, "ghp_xxx", "https_token"),
        ("git@github.com:foo/bar.git", 1, None, "ssh_key"),
        ("ssh://git@github.com/foo/bar.git", 1, None, "ssh_key"),
    ],
)
def test_derive_auth_type_recognises_all_paths(url, ssh_key_id, https_token, expected):
    assert derive_auth_type(url, ssh_key_id, https_token) == expected


def test_derive_auth_type_rejects_ssh_url_without_key():
    with pytest.raises(ValueError, match="SSH URLs require an SSH key"):
        derive_auth_type("git@github.com:foo/bar.git", None, None)


def test_derive_auth_type_rejects_unknown_scheme():
    with pytest.raises(ValueError, match="https://, ssh://, or git@"):
        derive_auth_type("ftp://example.com/repo.git", None, None)


# ---------------------------------------------------------------------------
# URL host validation (SEC-11) — pure-function unit tests on the schema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/foo/bar.git",
        "git@github.com:foo/bar.git",
        "ssh://git@server.example.com/path/to/repo.git",
        # RFC1918 must be ALLOWED — operators may run git servers on LAN.
        "https://192.168.1.10/repo.git",
        "https://10.0.0.5/configs.git",
        "https://172.16.20.1/repo.git",
    ],
)
def test_validate_url_accepts_legitimate_urls(url):
    needs_key = url.startswith(("git@", "ssh://"))
    repo = GitRepoCreate(name="r", url=url, ssh_key_id=1 if needs_key else None)
    assert repo.url == url


@pytest.mark.parametrize(
    ("url", "match"),
    [
        ("https://127.0.0.1/x", "blocked"),
        ("https://127.255.255.255/x", "blocked"),
        ("https://localhost/x", "blocked"),
        ("https://LOCALHOST/x", "blocked"),
        ("https://169.254.169.254/x", "blocked"),
        ("https://169.254.0.1/x", "blocked"),
        # IPv6 forms
        ("ssh://[::1]/repo.git", "blocked"),
        ("https://[fe80::1]/x", "blocked"),
        # scp-style SSH with loopback host
        ("git@127.0.0.1:org/repo.git", "blocked"),
    ],
)
def test_validate_url_rejects_blocked_hosts(url, match):
    with pytest.raises(ValueError, match=match):
        GitRepoCreate(name="r", url=url)


# ---------------------------------------------------------------------------
# API integration — uses the standard superuser_client fixture
# ---------------------------------------------------------------------------


async def test_create_public_https_repo_persists_as_none(superuser_client):
    body = {
        "name": "public-pack",
        "url": "https://github.com/open-labdog/labdog-playbooks",
        "branch": "main",
    }
    resp = await superuser_client.post("/api/git-repos", json=body)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["auth_type"] == "none"
    assert data["ssh_key_id"] is None


async def test_create_https_with_token_persists_as_https_token(superuser_client):
    body = {
        "name": "private-pack",
        "url": "https://gitea.example.com/team/configs.git",
        "branch": "main",
        "https_token": "tok_secret",
    }
    resp = await superuser_client.post("/api/git-repos", json=body)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["auth_type"] == "https_token"
    # Response must never carry the plaintext or encrypted token.
    assert "https_token" not in data
    assert "encrypted_https_token" not in data


async def test_create_ssh_url_without_key_returns_400(superuser_client):
    body = {
        "name": "no-key",
        "url": "git@github.com:org/repo.git",
        "branch": "main",
    }
    resp = await superuser_client.post("/api/git-repos", json=body)
    assert resp.status_code == 400
    assert "SSH key" in resp.json()["detail"]


async def test_create_ssh_url_with_key_persists_as_ssh_key(superuser_client, db):
    """Needs a real SSH key row to point at."""
    from app.models.ssh_key import SSHKey

    key = SSHKey(
        name="deploy-key",
        encrypted_private_key=b"\x00" * 32,
    )
    db.add(key)
    await db.flush()

    body = {
        "name": "ssh-pack",
        "url": "git@github.com:org/repo.git",
        "branch": "main",
        "ssh_key_id": key.id,
    }
    resp = await superuser_client.post("/api/git-repos", json=body)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["auth_type"] == "ssh_key"
    assert data["ssh_key_id"] == key.id


async def test_update_clearing_token_falls_back_to_none(superuser_client):
    create = await superuser_client.post(
        "/api/git-repos",
        json={
            "name": "switch",
            "url": "https://example.com/r.git",
            "branch": "main",
            "https_token": "tok_a",
        },
    )
    repo_id = create.json()["id"]

    # Updating with no URL/token change keeps the existing token —
    # auth_type stays https_token.
    resp = await superuser_client.put(
        f"/api/git-repos/{repo_id}",
        json={"branch": "develop"},
    )
    assert resp.status_code == 200
    assert resp.json()["auth_type"] == "https_token"


async def test_update_switching_url_to_ssh_requires_key(superuser_client):
    create = await superuser_client.post(
        "/api/git-repos",
        json={
            "name": "switch2",
            "url": "https://example.com/r.git",
            "branch": "main",
        },
    )
    repo_id = create.json()["id"]

    # Switching to SSH without supplying a key must be rejected.
    resp = await superuser_client.put(
        f"/api/git-repos/{repo_id}",
        json={"url": "git@example.com:org/r.git"},
    )
    assert resp.status_code == 400
    assert "SSH key" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Audit log assertions (SEC-14)
# ---------------------------------------------------------------------------


async def test_create_git_repo_emits_audit_row(superuser_client, db):
    from sqlalchemy import select

    from app.models.audit_log import AuditLog

    resp = await superuser_client.post(
        "/api/git-repos",
        json={
            "name": "audit-create-repo",
            "url": "https://github.com/open-labdog/labdog-playbooks",
            "branch": "main",
        },
    )
    assert resp.status_code == 201, resp.text
    repo_id = resp.json()["id"]

    row = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.action == "create",
                AuditLog.entity_type == "git_repository",
                AuditLog.entity_id == repo_id,
            )
        )
    ).scalar_one_or_none()

    assert row is not None
    assert row.after_state["name"] == "audit-create-repo"
    assert row.after_state["url"] == "https://github.com/open-labdog/labdog-playbooks"
    assert row.after_state["branch"] == "main"
    assert row.after_state["auth_type"] == "none"
    # Secret material must never appear in the audit payload.
    assert "https_token" not in row.after_state
    assert "encrypted_https_token" not in row.after_state
    assert "webhook_secret" not in row.after_state
    assert row.before_state is None


async def test_update_git_repo_emits_audit_row(superuser_client, db):
    from sqlalchemy import select

    from app.models.audit_log import AuditLog

    create = await superuser_client.post(
        "/api/git-repos",
        json={
            "name": "audit-update-repo",
            "url": "https://github.com/open-labdog/labdog-playbooks",
            "branch": "main",
        },
    )
    repo_id = create.json()["id"]

    await superuser_client.put(
        f"/api/git-repos/{repo_id}",
        json={"branch": "develop"},
    )

    row = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.action == "update",
                AuditLog.entity_type == "git_repository",
                AuditLog.entity_id == repo_id,
            )
        )
    ).scalar_one_or_none()

    assert row is not None
    assert row.before_state["branch"] == "main"
    assert row.after_state["branch"] == "develop"
    assert row.after_state["name"] == "audit-update-repo"
    assert "https_token" not in row.after_state
    assert "encrypted_https_token" not in row.after_state
    assert "webhook_secret" not in row.after_state


async def test_delete_git_repo_emits_audit_row(superuser_client, db):
    from sqlalchemy import select

    from app.models.audit_log import AuditLog

    create = await superuser_client.post(
        "/api/git-repos",
        json={
            "name": "audit-delete-repo",
            "url": "https://github.com/open-labdog/labdog-playbooks",
            "branch": "main",
        },
    )
    repo_id = create.json()["id"]

    resp = await superuser_client.delete(f"/api/git-repos/{repo_id}")
    assert resp.status_code == 204

    row = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.action == "delete",
                AuditLog.entity_type == "git_repository",
                AuditLog.entity_id == repo_id,
            )
        )
    ).scalar_one_or_none()

    assert row is not None
    assert row.before_state["name"] == "audit-delete-repo"
    assert row.before_state["url"] == "https://github.com/open-labdog/labdog-playbooks"
    assert row.after_state is None
    assert "https_token" not in row.before_state
    assert "encrypted_https_token" not in row.before_state
    assert "webhook_secret" not in row.before_state
