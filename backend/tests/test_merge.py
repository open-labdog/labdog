"""
Integration tests for the effective-rules API endpoint.

Tests the GET /api/hosts/{host_id}/effective-rules endpoint which merges
rules from multiple groups with priority-based conflict resolution.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import create_group, create_host, create_rule, create_ssh_key

pytestmark = pytest.mark.integration


class TestMerge:
    """Integration tests for effective-rules API endpoint."""

    @pytest.mark.asyncio
    async def test_effective_rules_single_group(self, superuser_client, db: AsyncSession):
        """
        Test effective-rules with a single group.

        Creates a group with 2 rules, assigns host to group, and verifies:
        - Response is 200
        - Response includes the created rules
        - System SSH lockout rule is auto-injected
        """
        # Setup: create SSH key, group, rules, and host
        key = await create_ssh_key(db)
        group = await create_group(db, priority=100)

        # Create 2 rules in the group
        await create_rule(
            db,
            group_id=group.id,
            action="allow",
            protocol="tcp",
            direction="input",
            port_start=80,
            port_end=80,
            comment="Allow HTTP",
        )
        await create_rule(
            db,
            group_id=group.id,
            action="allow",
            protocol="tcp",
            direction="input",
            port_start=443,
            port_end=443,
            comment="Allow HTTPS",
        )

        # Create host and assign to group
        host = await create_host(db, ssh_key_id=key.id, group_ids=[group.id])

        # Act: GET effective-rules
        resp = await superuser_client.get(f"/api/hosts/{host.id}/effective-rules")

        # Assert
        assert resp.status_code == 200
        rules = resp.json()
        assert isinstance(rules, list)
        assert len(rules) >= 3  # At least: SSH lockout + 2 created rules

        # Verify SSH lockout rule is present
        ssh_rules = [r for r in rules if r.get("is_system")]
        assert len(ssh_rules) >= 1
        assert ssh_rules[0]["port_start"] == 22
        assert ssh_rules[0]["action"] == "allow"

        # Verify created rules are present
        port_80_rules = [r for r in rules if r.get("port_start") == 80]
        assert len(port_80_rules) >= 1
        assert port_80_rules[0]["action"] == "allow"

        port_443_rules = [r for r in rules if r.get("port_start") == 443]
        assert len(port_443_rules) >= 1
        assert port_443_rules[0]["action"] == "allow"

    @pytest.mark.asyncio
    async def test_effective_rules_priority_merge(self, superuser_client, db: AsyncSession):
        """
        Test priority-based merge when multiple groups have conflicting rules.

        Creates 2 groups with different priorities, each with a rule for port 22
        but different actions. Verifies that the higher priority group's action wins.
        """
        # Setup: create SSH key
        key = await create_ssh_key(db)

        # Create group with priority 100 (lower)
        group_low = await create_group(db, priority=100)
        await create_rule(
            db,
            group_id=group_low.id,
            action="allow",
            protocol="tcp",
            direction="input",
            port_start=8080,
            port_end=8080,
            comment="Low priority allow 8080",
        )

        # Create group with priority 200 (higher)
        group_high = await create_group(db, priority=200)
        await create_rule(
            db,
            group_id=group_high.id,
            action="deny",
            protocol="tcp",
            direction="input",
            port_start=8080,
            port_end=8080,
            comment="High priority deny 8080",
        )

        # Create host assigned to both groups
        host = await create_host(db, ssh_key_id=key.id, group_ids=[group_low.id, group_high.id])

        # Act: GET effective-rules
        resp = await superuser_client.get(f"/api/hosts/{host.id}/effective-rules")

        # Assert
        assert resp.status_code == 200
        rules = resp.json()

        # Find rules for port 8080 (excluding system rules)
        port_8080_rules = [
            r for r in rules if r.get("port_start") == 8080 and not r.get("is_system")
        ]

        # Should have exactly 1 rule for port 8080 (higher priority wins)
        assert len(port_8080_rules) == 1
        # Higher priority group (200) has action "deny", so that should win
        assert port_8080_rules[0]["action"] == "deny"

    @pytest.mark.asyncio
    async def test_effective_rules_no_groups(self, superuser_client, db: AsyncSession):
        """
        Test effective-rules for a host with NO group assignments.

        Verifies that even with no groups, the system SSH lockout rule is returned.
        """
        # Setup: create SSH key and host with NO groups
        key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=key.id, group_ids=[])

        # Act: GET effective-rules
        resp = await superuser_client.get(f"/api/hosts/{host.id}/effective-rules")

        # Assert
        assert resp.status_code == 200
        rules = resp.json()

        # Should have at least the SSH lockout rule
        assert len(rules) >= 1
        assert isinstance(rules, list)

        # Verify SSH lockout rule is present
        ssh_rules = [r for r in rules if r.get("is_system")]
        assert len(ssh_rules) >= 1
        assert ssh_rules[0]["port_start"] == 22
        assert ssh_rules[0]["action"] == "allow"
