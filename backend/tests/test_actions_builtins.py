"""Tests for built-in pseudo-actions and the underscore-key gate.

Covers C2 scope: the three ``_builtin.*`` ActionDefinitions are present
in ``ACTION_REGISTRY`` after import, the ``ActionManifest`` field
validator rejects underscore-prefixed keys, and the pack loader's
defence-in-depth skip path drops underscore-prefixed manifests with a
warning rather than registering them.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.actions.builtins import (
    BUILTIN_DEFINITIONS,
    BUILTIN_PACK_NAME,
    register_builtins,
)
from app.actions.manifest import ActionManifest
from app.actions.packs import Pack, load_pack
from app.actions.registry import ACTION_REGISTRY


def test_three_builtins_registered() -> None:
    assert "_builtin.sync" in ACTION_REGISTRY
    assert "_builtin.drift_check" in ACTION_REGISTRY
    assert "_builtin.collect_state" in ACTION_REGISTRY


def test_builtins_are_marked_builtin_with_no_playbook() -> None:
    for defn in BUILTIN_DEFINITIONS:
        assert defn.is_builtin is True
        assert defn.playbook_path is None
        assert defn.pack_name == BUILTIN_PACK_NAME


def test_supports_fleet_only_for_drift_and_state() -> None:
    assert ACTION_REGISTRY["_builtin.sync"].supports_fleet is False
    assert ACTION_REGISTRY["_builtin.drift_check"].supports_fleet is True
    assert ACTION_REGISTRY["_builtin.collect_state"].supports_fleet is True


def test_register_builtins_overwrites_existing_keys() -> None:
    """If a pack somehow contributes ``_builtin.sync``, built-ins win."""
    fake_registry: dict = {
        "_builtin.sync": object(),  # standin — built-ins should overwrite
        "linux-upgrade": object(),
    }
    register_builtins(fake_registry)
    assert fake_registry["_builtin.sync"] is ACTION_REGISTRY["_builtin.sync"]
    # Non-builtin keys are left alone.
    assert "linux-upgrade" in fake_registry


def test_manifest_rejects_underscore_prefixed_key() -> None:
    raw = {
        "key": "_my-pack-action",
        "name": "Bad",
        "description": "x",
        "icon": "Box",
        "playbook": "x.yml",
        "version": "1.0",
        "estimated_duration": "1m",
    }
    with pytest.raises(Exception) as exc:
        ActionManifest.model_validate(raw)
    assert "reserved" in str(exc.value).lower()


def test_pack_loader_skips_underscore_keyed_manifests(tmp_path: Path) -> None:
    """Belt-and-braces: even if a malformed YAML somehow produced an
    underscore-keyed model (it can't through the validator), the loader
    drops it with a warning. We exercise the skip path by writing a
    manifest whose YAML contains an underscore key — the validator will
    fail first, but we want the loader to log + continue not crash.
    """
    pack_dir = tmp_path / "evil-pack"
    actions_dir = pack_dir / "actions"
    actions_dir.mkdir(parents=True)

    # Bad manifest with reserved-prefix key (rejected by validator).
    (actions_dir / "_evil.manifest.yml").write_text(
        yaml.safe_dump(
            {
                "key": "_evil",
                "name": "x",
                "description": "x",
                "icon": "Box",
                "playbook": "evil.yml",
                "version": "1.0",
                "estimated_duration": "1m",
            }
        )
    )
    # A second, valid manifest in the same pack — must still load.
    (actions_dir / "good.manifest.yml").write_text(
        yaml.safe_dump(
            {
                "key": "good",
                "name": "Good",
                "description": "ok",
                "icon": "Box",
                "playbook": "good.yml",
                "version": "1.0",
                "estimated_duration": "1m",
            }
        )
    )
    (actions_dir / "good.yml").write_text("---\n- name: x\n  hosts: all\n  tasks: []\n")

    pack = Pack(name="evil-pack", path=pack_dir, priority=10)
    defns = load_pack(pack)

    keys = [d.key for d in defns]
    assert "_evil" not in keys  # rejected
    assert "good" in keys  # the valid sibling still loaded


def test_action_definition_supports_fleet_default_is_false() -> None:
    """Pack-supplied actions default conservatively to supports_fleet=False."""
    bundled_keys = [
        d for d in ACTION_REGISTRY.values() if not d.is_builtin
    ]
    # The bundled pack's existing actions don't set supports_fleet.
    for defn in bundled_keys:
        assert defn.supports_fleet is False, (
            f"{defn.key} unexpectedly has supports_fleet=True"
        )
