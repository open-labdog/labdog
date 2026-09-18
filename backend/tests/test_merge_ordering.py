"""BUG-69: the merge engines must produce one, stable answer.

Every module resolves a host's effective configuration by walking the
groups it belongs to in ``priority DESC`` order and letting the first
occurrence of a merge key win. Ordering by ``priority`` alone left the
winner to whatever order Postgres happened to return rows in, so two
groups sharing a priority gave a host in both a winner that could flip
between syncs with no configuration change — and nothing surfaced it,
because the diff engines compare sets and a re-ordering is not "drift".

Two halves, tested separately:

* ``host_groups.priority`` is unique now (migration ``0033``), so the tie
  cannot be created in the first place.
* The queries still spell out a total order — ``priority DESC, id ASC``
  for groups, and the same for the rules *inside* a group, which have no
  unique constraint on their merge key and, for firewall rules, decide
  the first-match order of the emitted ruleset.
"""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.host_group import HostGroup
from tests.conftest import create_group, create_host, create_rule

pytestmark = pytest.mark.integration


def _name() -> str:
    return f"g-{uuid.uuid4().hex[:8]}"


class TestGroupPriorityIsUnique:
    async def test_the_database_refuses_a_second_group_at_the_same_priority(self, db):
        """The application pre-check is a nicety; this is the guarantee."""
        await create_group(db, name=_name(), priority=500)
        db.add(HostGroup(name=_name(), priority=500))
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()

    async def test_a_racing_create_answers_409_rather_than_500(self, db):
        """Two concurrent creates can both pass the read-then-write check.

        ``_flush_or_conflict`` is what turns the loser's constraint
        violation into the 409 the handler always meant to return.
        """
        from fastapi import HTTPException

        from app.api.groups import _flush_or_conflict

        await create_group(db, name=_name(), priority=501)
        db.add(HostGroup(name=_name(), priority=501))
        with pytest.raises(HTTPException) as exc:
            await _flush_or_conflict(db)
        assert exc.value.status_code == 409
        assert "priority" in exc.value.detail

    async def test_a_racing_create_reports_a_duplicate_name_as_well(self, db):
        from fastapi import HTTPException

        from app.api.groups import _flush_or_conflict

        taken = _name()
        await create_group(db, name=taken, priority=502)
        db.add(HostGroup(name=taken, priority=503))
        with pytest.raises(HTTPException) as exc:
            await _flush_or_conflict(db)
        assert exc.value.status_code == 409
        assert "name" in exc.value.detail

    async def test_the_api_rejects_a_duplicate_priority(self, superuser_client, db):
        await create_group(db, name=_name(), priority=504)
        await db.commit()
        resp = await superuser_client.post("/api/groups", json={"name": _name(), "priority": 504})
        assert resp.status_code == 409


class TestGroupsAreWalkedHighestPriorityFirst:
    async def test_ordered_groups_for_host_sorts_by_priority_then_id(self, db):
        from app.merge_utils import ordered_groups_for_host

        low = await create_group(db, name=_name(), priority=10)
        high = await create_group(db, name=_name(), priority=20)
        host = await create_host(db, group_ids=[low.id, high.id])

        rows = (await db.execute(ordered_groups_for_host(host.id))).all()
        assert [r.group_id for r in rows] == [high.id, low.id]

    async def test_the_highest_priority_group_wins_the_merge_key(self, db):
        from app.cron.merge import get_effective_cron_jobs
        from app.cron.models import CronJob

        low = await create_group(db, name=_name(), priority=10)
        high = await create_group(db, name=_name(), priority=20)
        host = await create_host(db, group_ids=[low.id, high.id])
        for group_id, command in ((low.id, "loser"), (high.id, "winner")):
            db.add(
                CronJob(
                    group_id=group_id,
                    name="backup",
                    user="root",
                    schedule="0 3 * * *",
                    command=command,
                )
            )
        await db.flush()

        jobs = await get_effective_cron_jobs(host.id, db)
        assert [j.command for j in jobs] == ["winner"]
        assert jobs[0].source_id == high.id


class TestRulesInsideAGroupHaveATotalOrder:
    """No per-group unique constraint on the merge key, so two rules can
    collide inside one group. First-wins then picks between them, and the
    pick has to be the same on every sync."""

    async def test_cron_the_higher_rule_priority_wins(self, db):
        from app.cron.merge import get_effective_cron_jobs
        from app.cron.models import CronJob

        group = await create_group(db, name=_name())
        host = await create_host(db, group_ids=[group.id])
        # Inserted lowest-priority first, so insertion order is the wrong answer.
        for command, priority in (("loser", 1), ("winner", 9)):
            db.add(
                CronJob(
                    group_id=group.id,
                    name="backup",
                    user="root",
                    schedule="0 3 * * *",
                    command=command,
                    priority=priority,
                )
            )
        await db.flush()

        jobs = await get_effective_cron_jobs(host.id, db)
        assert [j.command for j in jobs] == ["winner"]

    async def test_cron_equal_priorities_fall_back_to_the_older_rule(self, db):
        from app.cron.merge import get_effective_cron_jobs
        from app.cron.models import CronJob

        group = await create_group(db, name=_name())
        host = await create_host(db, group_ids=[group.id])
        first = CronJob(
            group_id=group.id,
            name="backup",
            user="root",
            schedule="0 3 * * *",
            command="first",
        )
        db.add(first)
        await db.flush()
        db.add(
            CronJob(
                group_id=group.id,
                name="backup",
                user="root",
                schedule="0 3 * * *",
                command="second",
            )
        )
        await db.flush()

        jobs = await get_effective_cron_jobs(host.id, db)
        assert [j.command for j in jobs] == ["first"], "id ASC is the tiebreak"

    async def test_services_equal_priorities_fall_back_to_the_older_rule(self, db):
        from app.services.merge import get_effective_services
        from app.services.models import ServiceRule

        group = await create_group(db, name=_name())
        host = await create_host(db, group_ids=[group.id])
        for enabled in (True, False):
            db.add(ServiceRule(group_id=group.id, service_name="nginx", enabled=enabled))
            await db.flush()

        services = await get_effective_services(host.id, db)
        assert [s.enabled for s in services] == [True]

    async def test_users_equal_priorities_fall_back_to_the_older_rule(self, db):
        from app.user_mgmt.merge import get_effective_users
        from app.user_mgmt.models import LinuxUser

        group = await create_group(db, name=_name())
        host = await create_host(db, group_ids=[group.id])
        for shell in ("/bin/bash", "/bin/zsh"):
            db.add(LinuxUser(group_id=group.id, username="deploy", shell=shell))
            await db.flush()

        users = await get_effective_users(host.id, db)
        assert [u.shell for u in users] == ["/bin/bash"]


class TestTheFirewallRulesetOrderIsStable:
    """``FirewallRule.priority`` defaults to 0 for every rule, so within a
    group the first-match order of the emitted nftables ruleset used to be
    whatever the SELECT returned. ``compute_diff`` is set-based and would
    never have flagged the re-ordering."""

    async def test_equal_priority_rules_come_back_in_creation_order(self, db):
        from sqlalchemy import update

        from app.models.firewall_rule import FirewallRule
        from app.rules.desired_state import get_desired_state

        group = await create_group(db, name=_name())
        host = await create_host(db, group_ids=[group.id])
        ports = [8003, 8001, 8002]
        rules = [await create_rule(db, group.id, port_start=port) for port in ports]

        # Editing one rule is the everyday way a heap's physical order stops
        # matching its id order. Whether it actually moves the tuple is up to
        # Postgres, which is the whole point: the emitted first-match order
        # must not depend on that.
        await db.execute(
            update(FirewallRule).where(FirewallRule.id == rules[0].id).values(comment="touched")
        )
        await db.flush()

        specs, _ = await get_desired_state(host.id, db, host_source_ip="10.9.9.9")
        emitted = [s.port_start for s in specs if s.port_start in ports]
        assert emitted == ports, "creation order, not SELECT order"

    async def test_a_higher_rule_priority_moves_a_rule_to_the_front(self, db):
        from app.rules.desired_state import get_desired_state

        group = await create_group(db, name=_name())
        host = await create_host(db, group_ids=[group.id])
        await create_rule(db, group.id, port_start=9001)
        await create_rule(db, group.id, port_start=9002, priority=50)

        specs, _ = await get_desired_state(host.id, db, host_source_ip="10.9.9.9")
        emitted = [s.port_start for s in specs if s.port_start in (9001, 9002)]
        assert emitted == [9002, 9001]

    def test_the_merge_breaks_a_full_tie_on_rule_id(self):
        from app.rules.merge import merge_group_rules
        from app.rules.model import FirewallRuleSpec

        def spec(port, rule_id):
            return FirewallRuleSpec(
                action="allow",
                protocol="tcp",
                direction="input",
                port_start=port,
                rule_id=rule_id,
                group_id=1,
            )

        # Handed to the merge in the reverse of the order it must emit.
        group = {"id": 1, "priority": 100, "rules": [spec(81, 7), spec(80, 3)]}
        merged = merge_group_rules([group], server_ip="10.0.0.1")
        assert [r.port_start for r in merged if r.port_start in (80, 81)] == [80, 81]

    def test_host_overrides_still_outrank_every_group_rule(self):
        from app.rules.merge import merge_group_rules
        from app.rules.model import FirewallRuleSpec

        group_rule = FirewallRuleSpec(
            action="allow",
            protocol="tcp",
            direction="input",
            port_start=80,
            rule_id=1,
            group_id=1,
            priority=999,
        )
        host_rule = FirewallRuleSpec(
            action="allow",
            protocol="udp",
            direction="input",
            port_start=53,
            rule_id=2,
            host_id=1,
        )
        merged = merge_group_rules(
            [{"id": 1, "priority": 100, "rules": [group_rule]}],
            server_ip="10.0.0.1",
            host_rules=[host_rule],
        )
        # [0] is the auto-injected anti-lockout rule.
        assert [r.port_start for r in merged[1:]] == [53, 80]

    def test_groups_of_equal_priority_are_broken_on_group_id(self):
        """Unreachable through the API since 0033, but the merge is also
        called with hand-built dicts (gitops preview, tests), so the total
        order does not depend on the constraint holding."""
        from app.rules.merge import merge_group_policies

        groups = [
            {
                "id": 9,
                "name": "late",
                "priority": 100,
                "rules": [],
                "input_policy": "accept",
            },
            {
                "id": 2,
                "name": "early",
                "priority": 100,
                "rules": [],
                "input_policy": "drop",
            },
        ]
        assert merge_group_policies(groups).input_source_group_id == 2
        assert merge_group_policies(list(reversed(groups))).input_source_group_id == 2
