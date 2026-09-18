"""SEC-28: the webhook secret is a credential and is stored like one.

``git_repositories.webhook_secret`` held its value in plaintext and
``GitRepoResponse`` returned it, under an inline comment reading "OK to
return — it's for webhook validation, not a credential". It is exactly a
credential: the HMAC key ``app/api/webhooks.py`` compares every inbound
push signature against. Anyone who could read it — any authenticated
user through the API, anyone with a database dump — could forge a push
and make LabDog import configuration from a commit of their choosing.

It was also the only secret in the codebase outside an ``encrypted_*``
column, which is what let the exception go unnoticed.

The two halves that matter are that the value does not come back out,
and that the verifiers still work against what went in. A secret nobody
can read is useless if it also stops authenticating real pushes.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from app.crypto import get_master_key
from app.gitops.webhook_secret import get_webhook_secret, set_webhook_secret
from app.models.git_repository import GitAuthType, GitRepository

SECRET = "s3cr3t-webhook-key"


def _repo(**kw) -> GitRepository:
    return GitRepository(
        name=kw.pop("name", "r"),
        url=kw.pop("url", "https://example.com/r.git"),
        branch="main",
        auth_type=GitAuthType.none,
        **kw,
    )


class TestTheValueIsNotStoredInTheClear:
    def test_the_column_holds_ciphertext(self):
        repo = _repo()
        set_webhook_secret(repo, SECRET)
        blob = repo.encrypted_webhook_secret
        assert isinstance(blob, bytes)
        assert SECRET.encode() not in blob

    def test_it_round_trips(self):
        repo = _repo()
        set_webhook_secret(repo, SECRET)
        assert get_webhook_secret(repo) == SECRET

    def test_two_repos_with_the_same_secret_do_not_share_ciphertext(self):
        """AES-GCM with a random nonce — identical plaintext must not be
        identifiable as identical from the stored bytes."""
        a, b = _repo(name="a"), _repo(name="b")
        set_webhook_secret(a, SECRET)
        set_webhook_secret(b, SECRET)
        assert a.encrypted_webhook_secret != b.encrypted_webhook_secret

    @pytest.mark.parametrize("value", [None, ""])
    def test_clearing_leaves_nothing_behind(self, value):
        repo = _repo()
        set_webhook_secret(repo, SECRET)
        set_webhook_secret(repo, value)
        assert repo.encrypted_webhook_secret is None
        assert get_webhook_secret(repo) is None

    def test_a_repo_without_one_reads_as_none(self):
        assert get_webhook_secret(_repo()) is None

    def test_a_wrong_master_key_raises_rather_than_reading_as_unset(self):
        """Returning None here would turn a key-management problem into
        "every webhook is unsigned", which fails open."""
        from cryptography.exceptions import InvalidTag

        from app.crypto import encrypt_ssh_key

        repo = _repo()
        repo.encrypted_webhook_secret = encrypt_ssh_key(SECRET, b"\x01" * 32)
        assert get_master_key() != b"\x01" * 32
        with pytest.raises(InvalidTag):
            get_webhook_secret(repo)

    def test_the_flag_the_api_reports_tracks_the_column(self):
        repo = _repo()
        assert repo.has_webhook_secret is False
        set_webhook_secret(repo, SECRET)
        assert repo.has_webhook_secret is True


class TestTheApiDoesNotHandItBack:
    async def test_creating_with_a_secret_does_not_echo_it(self, superuser_client):
        resp = await superuser_client.post(
            "/api/git-repos",
            json={
                "name": "wh-create",
                "url": "https://example.com/r.git",
                "branch": "main",
                "webhook_secret": SECRET,
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.text
        assert SECRET not in body
        assert resp.json()["has_webhook_secret"] is True

    async def test_listing_does_not_leak_it(self, superuser_client):
        await superuser_client.post(
            "/api/git-repos",
            json={
                "name": "wh-list",
                "url": "https://example.com/l.git",
                "branch": "main",
                "webhook_secret": SECRET,
            },
        )
        resp = await superuser_client.get("/api/git-repos")
        assert SECRET not in resp.text
        assert any(r["has_webhook_secret"] for r in resp.json())

    async def test_a_repo_without_one_says_so(self, superuser_client):
        resp = await superuser_client.post(
            "/api/git-repos",
            json={"name": "wh-none", "url": "https://example.com/n.git", "branch": "main"},
        )
        assert resp.json()["has_webhook_secret"] is False

    async def test_it_is_stored_encrypted_not_merely_hidden(self, superuser_client, db):
        """Omitting it from the response would be worth little on its own
        — a database dump is the other half of the exposure."""
        from sqlalchemy import select

        create = await superuser_client.post(
            "/api/git-repos",
            json={
                "name": "wh-at-rest",
                "url": "https://example.com/a.git",
                "branch": "main",
                "webhook_secret": SECRET,
            },
        )
        repo = (
            await db.execute(select(GitRepository).where(GitRepository.id == create.json()["id"]))
        ).scalar_one()
        assert SECRET.encode() not in repo.encrypted_webhook_secret
        assert get_webhook_secret(repo) == SECRET


class TestUpdateSemantics:
    async def test_a_new_secret_replaces_the_old_one(self, superuser_client, db):
        from sqlalchemy import select

        create = await superuser_client.post(
            "/api/git-repos",
            json={
                "name": "wh-replace",
                "url": "https://example.com/r.git",
                "branch": "main",
                "webhook_secret": SECRET,
            },
        )
        repo_id = create.json()["id"]
        await superuser_client.put(f"/api/git-repos/{repo_id}", json={"webhook_secret": "rotated"})
        repo = (
            await db.execute(select(GitRepository).where(GitRepository.id == repo_id))
        ).scalar_one()
        await db.refresh(repo)
        assert get_webhook_secret(repo) == "rotated"

    async def test_omitting_it_keeps_the_existing_one(self, superuser_client, db):
        """The UI cannot pre-fill a field it can no longer read, so an
        unrelated edit must not silently wipe the secret."""
        from sqlalchemy import select

        create = await superuser_client.post(
            "/api/git-repos",
            json={
                "name": "wh-keep",
                "url": "https://example.com/k.git",
                "branch": "main",
                "webhook_secret": SECRET,
            },
        )
        repo_id = create.json()["id"]
        resp = await superuser_client.put(f"/api/git-repos/{repo_id}", json={"branch": "develop"})
        assert resp.status_code == 200, resp.text
        repo = (
            await db.execute(select(GitRepository).where(GitRepository.id == repo_id))
        ).scalar_one()
        await db.refresh(repo)
        assert get_webhook_secret(repo) == SECRET


class TestTheVerifiersStillWork:
    """The regression half. A secret nobody can read is useless if it
    also stops authenticating real pushes."""

    @pytest.fixture
    async def repo(self, db):
        repo = _repo(name="wh-verify", url="https://example.com/hooks.git")
        set_webhook_secret(repo, SECRET)
        db.add(repo)
        await db.flush()
        return repo

    @staticmethod
    def _github_payload(url: str) -> dict:
        return {
            "repository": {"clone_url": url},
            "ref": "refs/heads/main",
            "after": "a" * 40,
        }

    async def test_a_correctly_signed_github_push_is_accepted(self, client, repo):
        payload = self._github_payload(repo.url)
        body = json.dumps(payload).encode()
        sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        resp = await client.post(
            "/api/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "push",
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "accepted"

    async def test_a_wrongly_signed_push_is_still_rejected(self, client, repo):
        payload = self._github_payload(repo.url)
        body = json.dumps(payload).encode()
        sig = "sha256=" + hmac.new(b"not-the-secret", body, hashlib.sha256).hexdigest()
        resp = await client.post(
            "/api/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "push",
            },
        )
        assert resp.status_code == 401

    async def test_gitlab_compares_the_decrypted_token(self, client, repo):
        payload = {
            "project": {"git_http_url": repo.url},
            "ref": "refs/heads/main",
            "checkout_sha": "b" * 40,
        }
        resp = await client.post(
            "/api/webhooks/gitlab", json=payload, headers={"X-Gitlab-Token": SECRET}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "accepted"

    async def test_a_repo_with_no_secret_configured_refuses(self, client, db):
        repo = _repo(name="wh-unset", url="https://example.com/unset.git")
        db.add(repo)
        await db.flush()
        body = json.dumps(self._github_payload(repo.url)).encode()
        resp = await client.post(
            "/api/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": "sha256=" + "0" * 64,
                "X-GitHub-Event": "push",
            },
        )
        assert resp.status_code == 401
