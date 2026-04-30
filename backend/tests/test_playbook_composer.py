"""Unit tests for the playbook composer.

Pure in-memory tests — no DB, no Celery, no real generators. Fragments
are constructed by hand to exercise the composer's contract.
"""

from __future__ import annotations

import copy

import pytest
import yaml

from app.tasks.playbook_composer import (
    CANONICAL_ORDER,
    HOSTS_SENTINEL,
    PlaybookFragment,
    _inject_tags,
    compose_playbook,
    fragment_cron,
)


def _frag(module: str, task_name: str = "stub") -> PlaybookFragment:
    """Build a minimal one-play, one-task fragment for `module`."""
    return PlaybookFragment(
        module=module,
        plays=[
            {
                "name": f"LabDog {module}",
                "hosts": HOSTS_SENTINEL,
                "tasks": [{"name": task_name, "ansible.builtin.debug": {"msg": "ok"}}],
            }
        ],
    )


def test_orders_plays_in_canonical_order_regardless_of_input():
    out = yaml.safe_load(
        compose_playbook([_frag("firewall"), _frag("services"), _frag("packages")])
    )
    assert [p["name"] for p in out] == [
        "LabDog packages",
        "LabDog services",
        "LabDog firewall",
    ]


def test_module_filter_selects_subset():
    out = yaml.safe_load(
        compose_playbook(
            [_frag("firewall"), _frag("services"), _frag("packages")],
            module_filter=["firewall"],
        )
    )
    assert len(out) == 1
    assert out[0]["name"] == "LabDog firewall"


def test_module_filter_silently_skips_modules_not_in_fragments():
    out = yaml.safe_load(
        compose_playbook([_frag("firewall")], module_filter=["firewall", "services"])
    )
    assert [p["name"] for p in out] == ["LabDog firewall"]


def test_empty_module_filter_raises():
    with pytest.raises(ValueError, match="module_filter must not be empty"):
        compose_playbook([_frag("firewall")], module_filter=[])


def test_none_module_filter_includes_all():
    out = yaml.safe_load(
        compose_playbook([_frag("firewall"), _frag("packages")], module_filter=None)
    )
    assert {p["name"] for p in out} == {"LabDog firewall", "LabDog packages"}


def test_hosts_sentinel_replaced_with_alias():
    out = yaml.safe_load(
        compose_playbook([_frag("firewall"), _frag("services")], hosts_alias="web01")
    )
    assert all(p["hosts"] == "web01" for p in out)


def test_tags_injected_per_module():
    out = yaml.safe_load(compose_playbook([_frag("firewall"), _frag("services")]))
    fw_tags = out[1]["tasks"][0]["tags"]
    svc_tags = out[0]["tasks"][0]["tags"]
    assert "firewall" in fw_tags
    assert "services" in svc_tags
    assert "firewall" not in svc_tags


def test_duplicate_fragment_raises():
    with pytest.raises(ValueError, match="duplicate fragment"):
        compose_playbook([_frag("firewall"), _frag("firewall")])


def test_unknown_module_raises():
    with pytest.raises(ValueError, match="unknown module"):
        compose_playbook([PlaybookFragment(module="not-a-module", plays=[])])


def test_inject_tags_does_not_mutate_input():
    fragment = _frag("firewall", task_name="original")
    snapshot = copy.deepcopy(fragment.plays)
    compose_playbook([fragment])
    assert fragment.plays == snapshot


def test_canonical_order_covers_seven_modules():
    assert len(CANONICAL_ORDER) == 7
    assert set(CANONICAL_ORDER) == {
        "packages",
        "resolver",
        "services",
        "hosts-file",
        "cron",
        "linux-users",
        "firewall",
    }


def test_existing_string_tag_is_preserved():
    fragment = PlaybookFragment(
        module="firewall",
        plays=[
            {
                "name": "x",
                "hosts": HOSTS_SENTINEL,
                "tasks": [
                    {
                        "name": "t",
                        "ansible.builtin.debug": {"msg": "ok"},
                        "tags": "preexisting",
                    }
                ],
            }
        ],
    )
    out = yaml.safe_load(compose_playbook([fragment]))
    assert out[0]["tasks"][0]["tags"] == ["firewall", "preexisting"]


def test_pre_and_post_tasks_are_tagged():
    fragment = PlaybookFragment(
        module="firewall",
        plays=[
            {
                "name": "x",
                "hosts": HOSTS_SENTINEL,
                "pre_tasks": [{"name": "p", "ansible.builtin.debug": {"msg": "p"}}],
                "tasks": [{"name": "t", "ansible.builtin.debug": {"msg": "t"}}],
                "post_tasks": [{"name": "po", "ansible.builtin.debug": {"msg": "po"}}],
            }
        ],
    )
    out = yaml.safe_load(compose_playbook([fragment]))
    assert "firewall" in out[0]["pre_tasks"][0]["tags"]
    assert "firewall" in out[0]["tasks"][0]["tags"]
    assert "firewall" in out[0]["post_tasks"][0]["tags"]


def test_inject_tags_helper_idempotent():
    plays = [
        {
            "name": "x",
            "hosts": HOSTS_SENTINEL,
            "tasks": [{"name": "t", "tags": ["firewall"]}],
        }
    ]
    once = _inject_tags(plays, "firewall")
    twice = _inject_tags(once, "firewall")
    assert twice[0]["tasks"][0]["tags"] == ["firewall"]


# ---------------------------------------------------------------------------
# fragment_cron
# ---------------------------------------------------------------------------


def test_fragment_cron_returns_valid_fragment():
    fragment = fragment_cron(
        cron_jobs=[
            {
                "name": "nightly-backup",
                "user": "root",
                "schedule": "0 3 * * *",
                "command": "/usr/local/bin/backup.sh",
                "state": "present",
            }
        ]
    )
    assert isinstance(fragment, PlaybookFragment)
    assert fragment.module == "cron"
    assert len(fragment.plays) == 1
    assert all(p["hosts"] == HOSTS_SENTINEL for p in fragment.plays)


def test_fragment_cron_emits_cron_module_tasks():
    fragment = fragment_cron(
        cron_jobs=[
            {
                "name": "nightly-backup",
                "user": "root",
                "schedule": "0 3 * * *",
                "command": "/usr/local/bin/backup.sh",
                "state": "present",
            }
        ]
    )
    tasks = fragment.plays[0]["tasks"]
    assert any("ansible.builtin.cron" in t for t in tasks)


def test_fragment_cron_composes_through_compose_playbook():
    fragment = fragment_cron(
        cron_jobs=[
            {
                "name": "j",
                "user": "root",
                "schedule": "*/5 * * * *",
                "command": "/bin/true",
                "state": "present",
            }
        ]
    )
    out = yaml.safe_load(compose_playbook([fragment], hosts_alias="web01"))
    assert out[0]["hosts"] == "web01"
    assert all("cron" in t["tags"] for t in out[0]["tasks"])


def test_fragment_cron_with_empty_jobs():
    fragment = fragment_cron(cron_jobs=[])
    assert fragment.module == "cron"
    assert fragment.plays[0]["tasks"] == []
    out = yaml.safe_load(compose_playbook([fragment]))
    assert out[0]["hosts"] == HOSTS_SENTINEL
