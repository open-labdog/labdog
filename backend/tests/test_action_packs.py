"""Tests for the action pack loader.

These are pure unit tests — no DB, no Celery — exercising the manifest
schema, the on-disk pack layout, and the per-key resolution contract.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from app.actions.manifest import ActionManifest
from app.actions.packs import (
    Pack,
    load_pack,
    load_packs_with_resolutions,
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
    assert d.verify_playbook_path == (tmp_path / "vp" / "actions" / "demo" / "verify.yml").resolve()
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
    pack = Pack(name="p1", path=tmp_path / "p1")
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


def test_contested_key_with_explicit_resolution_wins(tmp_path: Path):
    """When the operator pins a contested key to a specific pack, that
    pack's manifest is returned and the other pack appears in
    ``overridden_from`` for provenance."""
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
    bundled = Pack(name="bundled", path=tmp_path / "bundled", pack_id=None)
    user = Pack(name="user", path=tmp_path / "user", pack_id=1)

    # Operator pinned pack 1 ("user") for the demo key.
    result = load_packs_with_resolutions(
        [user, bundled],
        resolutions={"demo": 1},
        prior_winners={},
    )
    defn = result.registry["demo"]
    assert defn.name == "User demo"
    assert defn.pack_name == "user"
    assert defn.winning_pack_id == 1
    assert defn.overridden_from == ("bundled",)
    assert defn.playbook_path is not None
    assert defn.is_unresolved is False


def test_contested_key_with_no_resolution_is_unresolved(tmp_path: Path):
    """Pure per-key-pinning: contested keys without a resolution have
    no winner. The registry entry is a placeholder with no playbook
    and ``winning_pack_id=None``."""
    for name in ("a", "b", "c"):
        _write_pack(tmp_path, name, actions={"demo": SIMPLE_ACTION})
    packs = [
        Pack(name="a", path=tmp_path / "a", pack_id=1),
        Pack(name="b", path=tmp_path / "b", pack_id=2),
        Pack(name="c", path=tmp_path / "c", pack_id=3),
    ]
    result = load_packs_with_resolutions(packs, resolutions={}, prior_winners={})
    defn = result.registry["demo"]
    assert defn.is_unresolved is True
    assert defn.winning_pack_id is None
    assert defn.playbook_path is None
    # Every contributor listed for provenance.
    assert set(defn.overridden_from) == {"a", "b", "c"}
    # Unresolved keys do not get a snapshot row — they're "open
    # questions" the next rebuild treats fresh.
    assert "demo" not in result.new_snapshot


def test_uncontested_key_wins_automatically(tmp_path: Path):
    """A key contributed by exactly one pack wins with no resolution
    row needed; ``overridden_from`` is empty."""
    _write_pack(tmp_path, "p1", actions={"demo": SIMPLE_ACTION})
    pack = Pack(name="p1", path=tmp_path / "p1", pack_id=7)
    result = load_packs_with_resolutions([pack], resolutions={}, prior_winners={})
    defn = result.registry["demo"]
    assert defn.is_unresolved is False
    assert defn.winning_pack_id == 7
    assert defn.overridden_from == ()
    assert defn.playbook_path is not None
    assert result.new_snapshot["demo"] == 7


def test_freeze_on_fresh_conflict_pins_previous_winner(tmp_path: Path):
    """Two packs now declare a key that previously had one. The merge
    auto-pins the previous winner (from the snapshot) so behaviour
    doesn't silently flip into an unresolved state. The freeze entry
    is reported so the caller can persist a durable resolution row."""
    for name in ("old", "new"):
        _write_pack(tmp_path, name, actions={"demo": SIMPLE_ACTION})
    packs = [
        Pack(name="old", path=tmp_path / "old", pack_id=1),
        Pack(name="new", path=tmp_path / "new", pack_id=2),
    ]
    # Previous rebuild saw only `old` as a contributor.
    result = load_packs_with_resolutions(packs, resolutions={}, prior_winners={"demo": 1})
    defn = result.registry["demo"]
    assert defn.is_unresolved is False
    assert defn.winning_pack_id == 1
    assert result.fresh_freezes == {"demo": 1}


def test_stale_resolution_falls_through_to_unresolved(tmp_path: Path):
    """A pin pointing at a pack that no longer contributes the key is
    queued for deletion; the key becomes unresolved (no fallback)."""
    for name in ("a", "b"):
        _write_pack(tmp_path, name, actions={"demo": SIMPLE_ACTION})
    packs = [
        Pack(name="a", path=tmp_path / "a", pack_id=1),
        Pack(name="b", path=tmp_path / "b", pack_id=2),
    ]
    # Resolution pins pack id 99 — not a current contributor.
    result = load_packs_with_resolutions(packs, resolutions={"demo": 99}, prior_winners={})
    assert "demo" in result.stale_resolution_keys
    assert result.registry["demo"].is_unresolved is True


# ---------------------------------------------------------------------------
# SEC-12 — pack.path schema validator
# ---------------------------------------------------------------------------


def test_pack_path_accepts_empty_string():
    """Empty path means 'pack lives at the repo root' — must be allowed."""
    from app.packs.schemas import ActionPackCreate

    obj = ActionPackCreate(name="p", source_type="git", git_repository_id=1, path="")
    assert obj.path == ""


def test_pack_path_accepts_simple_relative():
    from app.packs.schemas import ActionPackCreate

    obj = ActionPackCreate(name="p", source_type="git", git_repository_id=1, path="subdir")
    assert obj.path == "subdir"


def test_pack_path_accepts_nested_relative():
    from app.packs.schemas import ActionPackCreate

    obj = ActionPackCreate(name="p", source_type="git", git_repository_id=1, path="a/b/c")
    assert obj.path == "a/b/c"


def test_pack_path_rejects_dotdot_traversal():
    import pytest
    from pydantic import ValidationError

    from app.packs.schemas import ActionPackCreate

    with pytest.raises(ValidationError, match=r"\.\.|traversal"):
        ActionPackCreate(name="p", source_type="git", git_repository_id=1, path="../etc")


def test_pack_path_rejects_embedded_dotdot():
    import pytest
    from pydantic import ValidationError

    from app.packs.schemas import ActionPackCreate

    with pytest.raises(ValidationError, match=r"\.\.|traversal"):
        ActionPackCreate(name="p", source_type="git", git_repository_id=1, path="a/../b")


def test_pack_path_rejects_leading_slash():
    import pytest
    from pydantic import ValidationError

    from app.packs.schemas import ActionPackCreate

    with pytest.raises(ValidationError, match="relative"):
        ActionPackCreate(name="p", source_type="git", git_repository_id=1, path="/abs")


def test_pack_path_rejects_backslash():
    import pytest
    from pydantic import ValidationError

    from app.packs.schemas import ActionPackCreate

    with pytest.raises(ValidationError, match="backslash"):
        ActionPackCreate(name="p", source_type="git", git_repository_id=1, path="a\\b")


def test_pack_path_rejects_nul_byte():
    import pytest
    from pydantic import ValidationError

    from app.packs.schemas import ActionPackCreate

    with pytest.raises(ValidationError, match="NUL"):
        ActionPackCreate(name="p", source_type="git", git_repository_id=1, path="a\x00b")


def test_pack_path_rejects_overlong():
    import pytest
    from pydantic import ValidationError

    from app.packs.schemas import ActionPackCreate

    with pytest.raises(ValidationError):
        ActionPackCreate(
            name="p",
            source_type="git",
            git_repository_id=1,
            path="a" * 513,
        )


def test_pack_path_update_also_validated():
    """ActionPackUpdate applies the same path validator."""
    import pytest
    from pydantic import ValidationError

    from app.packs.schemas import ActionPackUpdate

    with pytest.raises(ValidationError, match=r"\.\.|traversal"):
        ActionPackUpdate(path="../escape")


# ---------------------------------------------------------------------------
# SEC-12 — effective_path_for runtime containment check
# ---------------------------------------------------------------------------


def test_effective_path_for_raises_when_path_escapes_checkout(tmp_path):
    """If a symlink (or anything else) causes the resolved path to escape
    the checkout directory, ``effective_path_for`` raises ValueError."""
    from unittest.mock import MagicMock

    import pytest

    from app.packs.models import PackSourceType  # noqa: I001
    from app.packs.service import effective_path_for

    # Create a symlink inside the checkout that points outside it.
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (checkout / "evil-link").symlink_to(outside)

    # Use a simple mock so we avoid SQLAlchemy instrumentation.
    pack = MagicMock()
    pack.source_type = PackSourceType.GIT
    pack.id = 999
    pack.path = "evil-link"

    # Monkeypatch checkout_path_for so it returns our temp checkout.
    import app.packs.service as svc

    original = svc.checkout_path_for
    svc.checkout_path_for = lambda _id: checkout
    try:
        with pytest.raises(ValueError, match="escapes"):
            effective_path_for(pack)
    finally:
        svc.checkout_path_for = original


# ---------------------------------------------------------------------------
# SEC-13 — local_path schema validator
# ---------------------------------------------------------------------------


def test_local_path_accepts_srv():
    from app.packs.schemas import ActionPackCreate

    obj = ActionPackCreate(
        name="p",
        source_type="local",
        local_path="/srv/labdog-packs",
    )
    assert obj.local_path == "/srv/labdog-packs"


def test_local_path_accepts_opt():
    from app.packs.schemas import ActionPackCreate

    obj = ActionPackCreate(
        name="p",
        source_type="local",
        local_path="/opt/labdog/packs",
    )
    assert obj.local_path == "/opt/labdog/packs"


def test_local_path_accepts_home_subpath():
    from app.packs.schemas import ActionPackCreate

    obj = ActionPackCreate(
        name="p",
        source_type="local",
        local_path="/home/operator/packs",
    )
    assert obj.local_path == "/home/operator/packs"


def test_local_path_rejects_etc():
    import pytest
    from pydantic import ValidationError

    from app.packs.schemas import ActionPackCreate

    with pytest.raises(ValidationError, match="/etc"):
        ActionPackCreate(name="p", source_type="local", local_path="/etc")


def test_local_path_rejects_etc_subpath():
    import pytest
    from pydantic import ValidationError

    from app.packs.schemas import ActionPackCreate

    with pytest.raises(ValidationError, match="/etc"):
        ActionPackCreate(name="p", source_type="local", local_path="/etc/labdog")


def test_local_path_rejects_proc():
    import pytest
    from pydantic import ValidationError

    from app.packs.schemas import ActionPackCreate

    with pytest.raises(ValidationError, match="/proc"):
        ActionPackCreate(name="p", source_type="local", local_path="/proc/self")


def test_local_path_rejects_root_ssh():
    import pytest
    from pydantic import ValidationError

    from app.packs.schemas import ActionPackCreate

    with pytest.raises(ValidationError, match="/root"):
        ActionPackCreate(name="p", source_type="local", local_path="/root/.ssh")


def test_local_path_rejects_dev_null():
    import pytest
    from pydantic import ValidationError

    from app.packs.schemas import ActionPackCreate

    with pytest.raises(ValidationError, match="/dev"):
        ActionPackCreate(name="p", source_type="local", local_path="/dev/null")


def test_local_path_rejects_filesystem_root():
    import pytest
    from pydantic import ValidationError

    from app.packs.schemas import ActionPackCreate

    with pytest.raises(ValidationError, match="dangerous"):
        ActionPackCreate(name="p", source_type="local", local_path="/")


def test_local_path_rejects_relative_path():
    import pytest
    from pydantic import ValidationError

    from app.packs.schemas import ActionPackCreate

    with pytest.raises(ValidationError, match="absolute"):
        ActionPackCreate(name="p", source_type="local", local_path="relative/path")


def test_local_path_rejects_empty_string():
    import pytest
    from pydantic import ValidationError

    from app.packs.schemas import ActionPackCreate

    with pytest.raises(ValidationError, match="absolute"):
        ActionPackCreate(name="p", source_type="local", local_path="")


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
