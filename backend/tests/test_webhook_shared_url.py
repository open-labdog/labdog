"""BUG-141: two repository rows for one URL no longer break its webhooks.

Two ``GitRepository`` rows for one URL (two branches of one repository)
made ``scalar_one_or_none()`` raise ``MultipleResultsFound``, so every
delivery for that repository answered 500. Rows are now matched by URL,
verified with their own secret, and picked by branch.
"""

import hashlib
import hmac
import json
import uuid
from unittest.mock import patch

import pytest

from app.gitops.webhook_secret import set_webhook_secret
from app.models.git_repository import GitAuthType, GitRepository
from tests.conftest import create_group

pytestmark = pytest.mark.integration

URL = f"https://git.example/{uuid.uuid4().hex[:8]}/infra.git"


async def _repo(db, branch: str, secret: str) -> GitRepository:
    repo = GitRepository(
        name=f"infra-{branch}-{uuid.uuid4().hex[:6]}",
        url=URL,
        branch=branch,
        auth_type=GitAuthType.https_token,
    )
    set_webhook_secret(repo, secret)
    db.add(repo)
    await db.flush()
    group = await create_group(db, name=f"g-{uuid.uuid4().hex[:8]}")
    group.gitops_enabled = True
    group.git_repository_id = repo.id
    group.gitops_file_path = "groups/x.yaml"
    await db.flush()
    return repo


def _push(branch: str) -> bytes:
    return json.dumps(
        {
            "ref": f"refs/heads/{branch}",
            "after": "a" * 40,
            "deleted": False,
            "repository": {"clone_url": URL, "ssh_url": URL},
        }
    ).encode()


async def _deliver(external_client, body: bytes, secret: str):
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    sent: list[dict] = []
    with patch(
        "app.api.webhooks.celery_app.send_task",
        new=lambda name, kwargs=None, **_: sent.append(kwargs),
    ):
        resp = await external_client.post(
            "/api/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "push",
                "X-Hub-Signature-256": sig,
            },
        )
    return resp, sent


async def test_a_push_reaches_the_row_for_its_branch(external_client, db):
    main = await _repo(db, "main", "main-secret")
    await _repo(db, "dev", "dev-secret")

    resp, sent = await _deliver(external_client, _push("main"), "main-secret")

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "accepted"
    assert [kw["repo_id"] for kw in sent] == [main.id]


async def test_another_rows_secret_does_not_open_this_branch(external_client, db):
    await _repo(db, "main", "main-secret")
    await _repo(db, "dev", "dev-secret")

    resp, sent = await _deliver(external_client, _push("main"), "dev-secret")

    # Signed by the dev row's secret: authentic for dev, but dev's branch
    # does not match, so nothing is dispatched.
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"
    assert sent == []


async def test_a_wrong_signature_is_401(external_client, db):
    await _repo(db, "main", "main-secret")
    await _repo(db, "dev", "dev-secret")

    resp, sent = await _deliver(external_client, _push("main"), "nobodys-secret")

    assert resp.status_code == 401
    assert sent == []
