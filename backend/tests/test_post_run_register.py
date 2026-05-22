"""Tests for the post-run resource registration feature.

Covers:

* Manifest validates module-name keys + per-item fields through the
  existing per-module Create schemas.
* The dispatch helper inserts host-scope rows for each declared item,
  skips on uniqueness collision (operator-declared rows win), and
  fires a follow-up sync for the affected modules.

The end-to-end "action runs -> registration fires" wiring is not
covered here (full action_host pipeline setup is heavy); the
manifest contract + helper unit tests pin the moving pieces.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.actions.manifest import ActionManifest
from app.models.audit_log import AuditLog
from app.packages.models import PackageRule
from app.services.models import ServiceRule
from app.sync.post_run import dispatch_post_run_register
from tests.conftest import create_host, create_ssh_key

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------


class TestManifestPostRunRegister:
    BASE_KWARGS = {
        "key": "demo",
        "name": "Demo",
        "description": "demo",
        "icon": "Box",
        "playbook": "playbook.yml",
        "version": "1.0",
        "estimated_duration": "1 min",
    }

    def test_default_is_empty(self):
        m = ActionManifest(**self.BASE_KWARGS)
        assert m.post_run_register == {}

    def test_accepts_validated_items(self):
        m = ActionManifest(
            **self.BASE_KWARGS,
            post_run_register={
                "packages": [{"package_name": "alloy"}],
                "services": [
                    {
                        "service_name": "alloy.service",
                        "state": "running",
                        "enabled": True,
                    }
                ],
            },
        )
        # Defaults from the Create schemas are filled in -- the stored
        # dicts should be the full validated shape, not the raw input.
        # ServiceRuleCreate strips the ".service" suffix; that's
        # labdog's internal convention.
        assert m.post_run_register["packages"][0]["package_name"] == "alloy"
        assert m.post_run_register["packages"][0]["state"] == "present"
        assert m.post_run_register["services"][0]["service_name"] == "alloy"

    def test_rejects_unknown_module(self):
        with pytest.raises(ValidationError):
            ActionManifest(
                **self.BASE_KWARGS,
                post_run_register={"not-a-module": [{"name": "x"}]},
            )

    def test_rejects_invalid_item(self):
        # Missing required field on the per-module Create schema.
        with pytest.raises(ValidationError):
            ActionManifest(
                **self.BASE_KWARGS,
                post_run_register={"packages": [{"state": "present"}]},  # no package_name
            )

    def test_empty_module_list_dropped(self):
        m = ActionManifest(
            **self.BASE_KWARGS,
            post_run_register={"packages": []},
        )
        # Empty lists shouldn't survive validation -- they'd dispatch
        # nothing and just clutter the API response.
        assert m.post_run_register == {}


# ---------------------------------------------------------------------------
# dispatch_post_run_register helper
# ---------------------------------------------------------------------------


def _no_op_sync_delay():
    """Patch run_host_sync.delay so the helper's follow-up sync
    dispatch doesn't try to talk to the broker."""
    return patch(
        "app.tasks.host_sync_orchestrator.run_host_sync.delay",
        new=MagicMock(side_effect=lambda *a, **kw: None),
    )


class TestDispatchPostRunRegister:
    async def test_empty_declarations_is_noop(self, db):
        with _no_op_sync_delay():
            result = await dispatch_post_run_register(
                db, host_id=1, declarations={}, triggered_by_user_id=None
            )
        assert result == {}

    async def test_inserts_host_scope_rows(self, db):
        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh_key.id)

        with _no_op_sync_delay():
            inserted = await dispatch_post_run_register(
                db,
                host_id=host.id,
                declarations={
                    "packages": [{"package_name": "alloy", "state": "present"}],
                    "services": [
                        {
                            "service_name": "alloy.service",
                            "state": "running",
                            "enabled": True,
                            "deploy_mode": "override",
                        }
                    ],
                },
                triggered_by_user_id=None,
            )

        assert inserted == {"packages": 1, "services": 1}

        pkg = (
            await db.execute(
                select(PackageRule).where(
                    PackageRule.host_id == host.id,
                    PackageRule.package_name == "alloy",
                )
            )
        ).scalar_one()
        assert pkg.host_id == host.id
        assert pkg.group_id is None
        assert pkg.state == "present"

        svc = (
            await db.execute(
                select(ServiceRule).where(
                    ServiceRule.host_id == host.id,
                    # Service Create schema strips ".service" suffix.
                    ServiceRule.service_name == "alloy",
                )
            )
        ).scalar_one()
        assert svc.host_id == host.id
        assert svc.group_id is None
        assert svc.state == "running"

    async def test_skips_uniqueness_collision(self, db):
        """An operator-declared row blocks the action's declaration."""
        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh_key.id)

        # Operator already declared alloy with state=absent.
        pre_existing = PackageRule(
            host_id=host.id,
            package_name="alloy",
            state="absent",
            package_manager="auto",
            priority=0,
        )
        db.add(pre_existing)
        await db.commit()

        with _no_op_sync_delay():
            inserted = await dispatch_post_run_register(
                db,
                host_id=host.id,
                declarations={
                    "packages": [
                        {"package_name": "alloy", "state": "present"},
                        {"package_name": "grafana-agent", "state": "present"},
                    ]
                },
                triggered_by_user_id=None,
            )

        # alloy collided -> skipped; grafana-agent succeeded.
        assert inserted == {"packages": 1}

        # Operator's row is untouched (still state=absent).
        rows = (
            (await db.execute(select(PackageRule).where(PackageRule.host_id == host.id)))
            .scalars()
            .all()
        )
        by_name = {r.package_name: r for r in rows}
        assert by_name["alloy"].state == "absent"  # unchanged
        assert by_name["grafana-agent"].state == "present"

    async def test_dispatches_follow_up_sync_for_affected_modules(self, db):
        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh_key.id)
        delay_calls: list[tuple] = []

        with patch(
            "app.tasks.host_sync_orchestrator.run_host_sync.delay",
            new=MagicMock(side_effect=lambda *a, **kw: delay_calls.append((a, kw))),
        ):
            await dispatch_post_run_register(
                db,
                host_id=host.id,
                declarations={
                    "packages": [{"package_name": "alloy"}],
                    "services": [
                        {
                            "service_name": "alloy.service",
                            "state": "running",
                            "enabled": True,
                            "deploy_mode": "override",
                        }
                    ],
                },
                triggered_by_user_id=None,
            )

        # One follow-up sync per affected module.
        assert len(delay_calls) == 2
        modules = sorted(kw["module_filter"][0] for _, kw in delay_calls)
        assert modules == ["packages", "services"]

    async def test_skips_follow_up_sync_when_nothing_inserted(self, db):
        """If every insert collided, no follow-up sync should fire."""
        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh_key.id)

        # Pre-seed a colliding row for the only declared module.
        db.add(
            PackageRule(
                host_id=host.id,
                package_name="alloy",
                state="present",
                package_manager="auto",
                priority=0,
            )
        )
        await db.commit()

        delay_calls: list[tuple] = []
        with patch(
            "app.tasks.host_sync_orchestrator.run_host_sync.delay",
            new=MagicMock(side_effect=lambda *a, **kw: delay_calls.append((a, kw))),
        ):
            inserted = await dispatch_post_run_register(
                db,
                host_id=host.id,
                declarations={
                    "packages": [{"package_name": "alloy"}],
                },
                triggered_by_user_id=None,
            )

        assert inserted == {}
        assert delay_calls == []

    async def test_emits_audit_log_per_insert(self, db):
        """Each successful insert must produce a matching audit_log row."""
        from fastapi_users.password import PasswordHelper

        from app.models.user import User as UserModel

        # Create a real user so the FK on audit_log.user_id is satisfied.
        ph = PasswordHelper()
        user = UserModel(
            email="audit-test@example.com",
            hashed_password=ph.hash("TestPass1!"),
            is_active=True,
            is_superuser=True,
            is_verified=True,
        )
        db.add(user)
        await db.flush()
        user_id = user.id

        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh_key.id)

        with _no_op_sync_delay():
            await dispatch_post_run_register(
                db,
                host_id=host.id,
                declarations={
                    "packages": [
                        {"package_name": "alloy", "state": "present"},
                        {"package_name": "grafana-agent", "state": "present"},
                    ],
                    "services": [
                        {
                            "service_name": "alloy.service",
                            "state": "running",
                            "enabled": True,
                            "deploy_mode": "override",
                        }
                    ],
                },
                triggered_by_user_id=user_id,
            )

        audit_rows = (
            (await db.execute(select(AuditLog).where(AuditLog.action == "post_run_register")))
            .scalars()
            .all()
        )
        # Two package rows + one service row = three audit entries.
        assert len(audit_rows) == 3

        entity_types = sorted(r.entity_type for r in audit_rows)
        assert entity_types == ["packages", "packages", "services"]

        for row in audit_rows:
            assert row.user_id == user_id
            assert row.action == "post_run_register"
            assert row.entity_id is not None
            assert row.after_state is not None


# ---------------------------------------------------------------------------
# SEC-15 Part 2: reject authorized_keys in linux-users at manifest-validate time
# ---------------------------------------------------------------------------


class TestManifestLinuxUsersAuthorizedKeysRejection:
    BASE_KWARGS = {
        "key": "demo",
        "name": "Demo",
        "description": "demo",
        "icon": "Box",
        "playbook": "playbook.yml",
        "version": "1.0",
        "estimated_duration": "1 min",
    }

    def test_rejects_authorized_keys_in_linux_users(self):
        """A manifest declaring authorized_keys on linux-users must be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            ActionManifest(
                **self.BASE_KWARGS,
                post_run_register={
                    "linux-users": [
                        {
                            "username": "x",
                            "authorized_keys": ["ssh-ed25519 AAAA"],
                        }
                    ]
                },
            )
        assert "authorized_keys" in str(exc_info.value)

    def test_accepts_linux_users_without_authorized_keys(self):
        """A linux-users item without authorized_keys must pass validation."""
        m = ActionManifest(
            **self.BASE_KWARGS,
            post_run_register={
                "linux-users": [
                    {
                        "username": "deploy",
                        "shell": "/bin/bash",
                    }
                ]
            },
        )
        assert m.post_run_register["linux-users"][0]["username"] == "deploy"
        assert m.post_run_register["linux-users"][0]["authorized_keys"] == []
