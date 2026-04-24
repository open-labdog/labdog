"""Tests for the action pack loader.

These are pure unit tests — no DB, no Celery — exercising the manifest
schema, the on-disk pack layout, and the override-by-priority contract.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from app.actions.manifest import ActionManifest
from app.actions.packs import (
    Pack,
    load_pack,
    load_packs,
)


def _write_pack(
    root: Path,
    name: str,
    manifests: dict[str, str],
    playbooks: dict[str, str],
    *,
    pack_yml: str | None = None,
    roles: list[str] | None = None,
) -> Path:
    pack_dir = root / name
    (pack_dir / "actions").mkdir(parents=True)
    for fname, body in manifests.items():
        (pack_dir / "actions" / fname).write_text(body)
    for fname, body in playbooks.items():
        (pack_dir / "actions" / fname).write_text(body)
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
    playbook: demo.yml
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
            "playbook": "demo.yml",
            "version": "1.0",
            "estimated_duration": "1 min",
        }
    )
    assert m.destructive is False
    assert m.supports_group is True
    assert m.parameters == []


def test_manifest_rejects_unknown_fields():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ActionManifest.model_validate(
            {
                "key": "demo",
                "name": "Demo",
                "description": "d",
                "icon": "Box",
                "playbook": "demo.yml",
                "version": "1.0",
                "estimated_duration": "1 min",
                "mystery_field": "oops",
            }
        )


def test_load_pack_returns_action_definition(tmp_path: Path):
    _write_pack(
        tmp_path,
        "p1",
        manifests={"demo.manifest.yml": SIMPLE_MANIFEST},
        playbooks={"demo.yml": SIMPLE_PLAYBOOK},
        roles=["role-demo"],
    )
    pack = Pack(name="p1", path=tmp_path / "p1", priority=50)
    defns = load_pack(pack)
    assert len(defns) == 1
    d = defns[0]
    assert d.key == "demo"
    assert d.playbook_path == (tmp_path / "p1" / "actions" / "demo.yml").resolve()
    assert d.pack_name == "p1"
    assert d.roles_paths == (tmp_path / "p1" / "roles",)


def test_load_pack_skips_missing_playbook(tmp_path: Path, caplog):
    _write_pack(
        tmp_path,
        "p1",
        manifests={"demo.manifest.yml": SIMPLE_MANIFEST},
        playbooks={},
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
        manifests={
            "good.manifest.yml": SIMPLE_MANIFEST,
            "bad.manifest.yml": "key: [broken",
        },
        playbooks={"demo.yml": SIMPLE_PLAYBOOK},
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
        manifests={"demo.manifest.yml": bundled_manifest},
        playbooks={"demo.yml": SIMPLE_PLAYBOOK},
    )
    _write_pack(
        tmp_path,
        "user",
        manifests={"demo.manifest.yml": user_manifest},
        playbooks={"demo.yml": SIMPLE_PLAYBOOK},
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
            manifests={"demo.manifest.yml": SIMPLE_MANIFEST},
            playbooks={"demo.yml": SIMPLE_PLAYBOOK},
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
        manifests={"demo.manifest.yml": SIMPLE_MANIFEST},
        playbooks={"demo.yml": SIMPLE_PLAYBOOK},
    )
    pack = Pack(name="p1", path=tmp_path / "p1", priority=10)
    registry = load_packs([pack])
    assert registry["demo"].overridden_from == ()


def test_bundled_pack_exposes_expected_actions():
    """Sanity check — the shipped bundled pack still produces the three
    actions LabDog has always had. Protects against manifest regressions."""
    from app.actions.registry import ACTION_REGISTRY

    assert {"linux-upgrade", "linux-os-upgrade", "k8s-upgrade"} <= set(ACTION_REGISTRY)
    linux = ACTION_REGISTRY["linux-upgrade"]
    assert linux.destructive is True
    assert linux.supports_group is False
    assert linux.playbook_path.name == "linux-upgrade.yml"
    assert linux.playbook_path.is_file()
    param_keys = {p.key for p in linux.parameters}
    assert param_keys == {"auto_reboot", "reboot_timeout", "cleanup"}
