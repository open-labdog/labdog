"""Webhook endpoints must be reachable by senders that have no CSRF token.

These tests exist because the suite already had thorough coverage of the
webhook handlers and every one of them passed while, in production, every
webhook returned 403 before its handler ran.

The cause was the test client rather than the handlers: ``client``
auto-attaches ``X-CSRF-Token`` to mutating requests, so it satisfied the
CSRF middleware on behalf of senders that can never satisfy it
themselves. Grafana, GitHub, GitLab and Gitea have no session, no cookie
jar, and no way to obtain a double-submit token.

So these use ``external_client`` — a plain ASGI transport with no cookies
and no header injection — which is the only client shape that can observe
the bug. What they assert is narrow and deliberate: **not** that a webhook
succeeds, but that it gets far enough to be rejected on its *own*
credentials. A 401 from the handler proves the request reached it; a 403
from the middleware proves it did not.
"""

import pytest

pytestmark = pytest.mark.asyncio


async def test_csrf_middleware_does_not_block_webhooks(external_client):
    """A webhook POST with no CSRF token must not be rejected as CSRF.

    The distinction that matters is *which* rejection comes back. Any
    status is acceptable here except the middleware's 403 — the handler
    is entitled to refuse an unknown repository or a bad token, and does.
    """
    resp = await external_client.post(
        "/api/webhooks/gitlab",
        json={"project": {"git_http_url": "https://example.com/nobody/nothing.git"}},
    )

    assert resp.status_code != 403, (
        "CSRF middleware rejected a webhook. External senders cannot "
        "produce a CSRF token, so this endpoint is unreachable."
    )
    # Unknown repository — the handler ran and made its own decision.
    assert resp.json() == {"status": "ignored", "reason": "unknown repository"}


@pytest.mark.parametrize("endpoint", ["github", "gitlab", "gitea", "grafana-alerts"])
async def test_every_webhook_route_is_exempt(external_client, endpoint):
    """Every route under the prefix, not just the one that was noticed.

    Parametrised rather than written once because the bug was uniform:
    all of them were broken identically and for the same reason, and one
    added later would be too. ``grafana-alerts`` is the demonstration —
    it arrived with alert intake, after the exemption was written, and
    needed nothing done to it because the prefix already covered it.
    """
    resp = await external_client.post(f"/api/webhooks/{endpoint}", json={})

    assert resp.status_code != 403, f"/api/webhooks/{endpoint} is CSRF-blocked"


async def test_csrf_still_enforced_outside_the_webhook_prefix(external_client):
    """The exemption is a prefix, so prove it did not become a hole.

    A prefix match is broader than a list of literals, which is what makes
    it durable and also what makes this assertion necessary: an ordinary
    mutating API route must still be refused without a token.
    """
    resp = await external_client.post("/api/hosts/", json={"name": "x"})

    assert resp.status_code == 403
    assert resp.json()["detail"] == "CSRF token missing or invalid"


async def test_csrf_exemption_does_not_extend_to_lookalike_paths(external_client):
    """``/api/webhooksomething`` is not ``/api/webhooks/something``.

    The prefix ends in a slash for exactly this reason. Without it a route
    whose name merely starts with "webhooks" would silently inherit the
    exemption.
    """
    resp = await external_client.post("/api/webhooks-not-really", json={})

    assert resp.status_code == 403
    assert resp.json()["detail"] == "CSRF token missing or invalid"
