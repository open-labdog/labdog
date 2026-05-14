"""Tests for the action pack loader.

These are pure unit tests — no DB, no Celery — exercising the manifest
schema, the on-disk pack layout, and the override-by-priority contract.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from app.actions.manifest import ActionManifest
from app.actions.packs import (
    Pack,
    load_pack,
    load_packs,
)


def _write_pack(
    root: Path,
    name: str,
    actions: dict[str, dict[str, str]],
    *,
    pack_yml: str | None = None,
    roles: list[str] | None = None,
) -> Path:
    """Write a pack to disk in the L2 layout.

    ``actions`` maps each action's directory name to a dict of files
    rooted at that action's directory. Conventionally ``manifest.yml``
    and ``playbook.yml``, plus any sibling files (verify playbooks,
    private roles, etc.) referenced by the manifest.
    """
    pack_dir = root / name
    (pack_dir / "actions").mkdir(parents=True)
    for action_name, files in actions.items():
        action_dir = pack_dir / "actions" / action_name
        action_dir.mkdir(parents=True, exist_ok=True)
        for fname, body in files.items():
            target = action_dir / fname
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body)
    if pack_yml is not None:
        (pack_dir / "pack.yml").write_text(pack_yml)
    for role in roles or []:
        (pack_dir / "roles" / role).mkdir(parents=True)
    return pack_dir


SIMPLE_MANIFEST = textwrap.dedent(
    """\
    key: demo
    name: Demo action
    description: A demo.
    icon: Box
    playbook: playbook.yml
    version: "1.0"
    estimated_duration: "1 min"
    """
)
SIMPLE_PLAYBOOK = "---\n- name: demo\n  hosts: all\n  tasks: []\n"


def test_manifest_parses_minimal_fields():
    m = ActionManifest.model_validate(
        {
            "key": "demo",
            "name": "Demo",
            "description": "d",
            "icon": "Box",
            "playbook": "playbook.yml",
            "version": "1.0",
            "estimated_duration": "1 min",
        }
    )
    assert m.destructive is False
    assert m.supports_group is True
    assert m.parameters == []


def test_manifest_ignores_unknown_fields():
    """Unknown fields (e.g. retired ``execution_mode``) are silently
    dropped so old manifests on disk don't break a fresh build."""
    m = ActionManifest.model_validate(
        {
            "key": "demo",
            "name": "Demo",
            "description": "d",
            "icon": "Box",
            "playbook": "playbook.yml",
            "version": "1.0",
            "estimated_duration": "1 min",
            "mystery_field": "oops",
            "execution_mode": "cluster",
        }
    )
    assert not hasattr(m, "mystery_field")
    assert not hasattr(m, "execution_mode")


def test_manifest_accepts_verify_playbook_fields():
    m = ActionManifest.model_validate(
        {
            "key": "demo",
            "name": "Demo",
            "description": "d",
            "icon": "Box",
            "playbook": "playbook.yml",
            "version": "1.0",
            "estimated_duration": "1 min",
            "verify_playbook": "demo-verify.yml",
            "verify_timeout_seconds": 45,
        }
    )
    assert m.verify_playbook == "demo-verify.yml"
    assert m.verify_timeout_seconds == 45


def test_manifest_defaults_have_no_verify():
    m = ActionManifest.model_validate(
        {
            "key": "demo",
            "name": "Demo",
            "description": "d",
            "icon": "Box",
            "playbook": "playbook.yml",
            "version": "1.0",
            "estimated_duration": "1 min",
        }
    )
    assert m.verify_playbook is None
    assert m.verify_timeout_seconds == 300


def test_load_pack_resolves_verify_playbook(tmp_path: Path):
    manifest_body = SIMPLE_MANIFEST + "verify_playbook: verify.yml\n"
    _write_pack(
        tmp_path,
        "vp",
        actions={
            "demo": {
                "manifest.yml": manifest_body,
                "playbook.yml": SIMPLE_PLAYBOOK,
                "verify.yml": SIMPLE_PLAYBOOK,
            },
        },
    )
    pack = Pack(name="vp", path=tmp_path / "vp")
    defns = load_pack(pack)
    assert len(defns) == 1
    d = defns[0]
    assert d.verify_playbook_path is not None
    assert (
        d.verify_playbook_path
        == (tmp_path / "vp" / "actions" / "demo" / "verify.yml").resolve()
    )
    assert d.verify_timeout_seconds == 300


def test_load_pack_skips_when_verify_playbook_missing(tmp_path: Path, caplog):
    manifest_body = SIMPLE_MANIFEST + "verify_playbook: not-here.yml\n"
    _write_pack(
        tmp_path,
        "vp",
        actions={
            "demo": {
                "manifest.yml": manifest_body,
                "playbook.yml": SIMPLE_PLAYBOOK,
            },
        },
    )
    pack = Pack(name="vp", path=tmp_path / "vp")
    with caplog.at_level("ERROR"):
        defns = load_pack(pack)
    # The whole manifest is rejected — same treatment as a missing main
    # playbook. Better to catch typos at load time than at run time.
    assert defns == []
    assert any("verify_playbook" in r.message for r in caplog.records)


SIMPLE_ACTION = {"manifest.yml": SIMPLE_MANIFEST, "playbook.yml": SIMPLE_PLAYBOOK}


def test_load_pack_returns_action_definition(tmp_path: Path):
    _write_pack(
        tmp_path,
        "p1",
        actions={"demo": SIMPLE_ACTION},
        roles=["role-demo"],
    )
    pack = Pack(name="p1", path=tmp_path / "p1", priority=50)
    defns = load_pack(pack)
    assert len(defns) == 1
    d = defns[0]
    assert d.key == "demo"
    assert d.playbook_path == (tmp_path / "p1" / "actions" / "demo" / "playbook.yml").resolve()
    assert d.pack_name == "p1"
    assert d.roles_paths == (tmp_path / "p1" / "roles",)


def test_load_pack_skips_missing_playbook(tmp_path: Path, caplog):
    _write_pack(
        tmp_path,
        "p1",
        actions={"demo": {"manifest.yml": SIMPLE_MANIFEST}},
    )
    pack = Pack(name="p1", path=tmp_path / "p1")
    with caplog.at_level("ERROR"):
        defns = load_pack(pack)
    assert defns == []
    assert any("does not exist" in r.message for r in caplog.records)


def test_load_pack_skips_invalid_yaml(tmp_path: Path, caplog):
    _write_pack(
        tmp_path,
        "p1",
        actions={
            "good": SIMPLE_ACTION,
            "bad": {"manifest.yml": "key: [broken", "playbook.yml": SIMPLE_PLAYBOOK},
        },
    )
    pack = Pack(name="p1", path=tmp_path / "p1")
    with caplog.at_level("ERROR"):
        defns = load_pack(pack)
    keys = {d.key for d in defns}
    assert keys == {"demo"}


def test_higher_priority_pack_overrides_lower(tmp_path: Path):
    bundled_manifest = SIMPLE_MANIFEST.replace("Demo action", "Bundled demo")
    user_manifest = SIMPLE_MANIFEST.replace("Demo action", "User demo")
    _write_pack(
        tmp_path,
        "bundled",
        actions={"demo": {"manifest.yml": bundled_manifest, "playbook.yml": SIMPLE_PLAYBOOK}},
    )
    _write_pack(
        tmp_path,
        "user",
        actions={"demo": {"manifest.yml": user_manifest, "playbook.yml": SIMPLE_PLAYBOOK}},
    )
    bundled = Pack(name="bundled", path=tmp_path / "bundled", priority=0)
    user = Pack(name="user", path=tmp_path / "user", priority=100)

    registry = load_packs([user, bundled])
    assert registry["demo"].name == "User demo"
    assert registry["demo"].pack_name == "user"
    assert registry["demo"].overridden_from == ("bundled",)


def test_override_chain_records_full_history(tmp_path: Path):
    """Three packs contribute the same key; the surviving entry knows
    about both shadowed packs in processing order."""
    for name in ("bundled", "default", "user"):
        _write_pack(
            tmp_path,
            name,
            actions={"demo": SIMPLE_ACTION},
        )
    packs = [
        Pack(name="bundled", path=tmp_path / "bundled", priority=0),
        Pack(name="default", path=tmp_path / "default", priority=10),
        Pack(name="user", path=tmp_path / "user", priority=100),
    ]
    registry = load_packs(packs)
    assert registry["demo"].pack_name == "user"
    # Shadowed packs in processing order (lowest priority first).
    assert registry["demo"].overridden_from == ("bundled", "default")


def test_non_colliding_actions_have_no_override_history(tmp_path: Path):
    _write_pack(
        tmp_path,
        "p1",
        actions={"demo": SIMPLE_ACTION},
    )
    pack = Pack(name="p1", path=tmp_path / "p1", priority=10)
    registry = load_packs([pack])
    assert registry["demo"].overridden_from == ()


def test_bundled_pack_exposes_expected_actions():
    """Sanity check — the shipped bundled pack still produces the
    actions LabDog has always had. Protects against manifest regressions."""
    from app.actions.registry import ACTION_REGISTRY

    assert {"linux-upgrade", "linux-os-upgrade", "k8s-upgrade"} <= set(ACTION_REGISTRY)
    linux = ACTION_REGISTRY["linux-upgrade"]
    assert linux.destructive is True
    # linux-upgrade is now group-supported — package upgrades fan out
    # across hosts identically; restricting it to host-only was an
    # accident.
    assert linux.supports_group is True
    assert linux.playbook_path.name == "playbook.yml"
    assert linux.playbook_path.is_file()
    param_keys = {p.key for p in linux.parameters}
    assert param_keys == {"auto_reboot", "reboot_timeout", "cleanup"}
