"""Tests for ``app.packs.repo_scanner.scan_repository``.

Pure file-walking. Static fixture trees under
``backend/tests/fixtures/scanner/`` exercise:
- well-formed multi-pack tree with gitops files,
- root-only fallback (no ``pack.yml``, manifests at the root),
- empty repo,
- broken manifests + invalid gitops files.

No DB, no network, no testcontainers. Pytest's tmp_path is used for
edge cases that need a fresh tree.
"""

from __future__ import annotations

from pathlib import Path

from app.packs.repo_scanner import (
    DetectedGitopsFile,
    DetectedPack,
    ScanError,
    scan_repository,
)

FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "scanner"


# ---------------------------------------------------------------------------
# well-formed multi-pack tree
# ---------------------------------------------------------------------------


def test_scan_well_formed_multi_pack_tree():
    result = scan_repository(FIXTURES_ROOT / "well_formed", repo_name="example")

    # Two packs, in deterministic filesystem-sort order
    # (actions/k8s/ before actions/upgrade/).
    assert len(result.packs) == 2
    pack_paths = [p.path for p in result.packs]
    assert pack_paths == ["actions/k8s", "actions/upgrade"]

    k8s, upgrade = result.packs
    assert k8s.name == "k8s-pack"
    assert k8s.contributed_keys == ("k8s-upgrade",)
    assert k8s.pack_yml_present is True
    assert k8s.errors == ()

    assert upgrade.name == "upgrade-pack"
    assert upgrade.contributed_keys == ("linux-upgrade",)
    assert upgrade.pack_yml_present is True
    assert upgrade.errors == ()

    # Two gitops files; the third YAML at root (_global.yaml) has no
    # ``group:`` key and is therefore not surfaced as a finding.
    gitops_paths = sorted(g.path for g in result.gitops_files)
    assert gitops_paths == ["groups/database.yaml", "groups/web-servers.yaml"]
    assert all(g.errors == () for g in result.gitops_files)
    by_path = {g.path: g for g in result.gitops_files}
    assert by_path["groups/web-servers.yaml"].group_name == "web-servers"
    assert by_path["groups/database.yaml"].group_name == "database"

    assert result.scan_errors == []


def test_scan_excludes_yaml_inside_pack_directories():
    """``defaults.yml`` lives inside a pack's ``actions/`` and must not be
    surfaced as a gitops finding even though it parses as YAML."""
    result = scan_repository(FIXTURES_ROOT / "well_formed")
    paths = {g.path for g in result.gitops_files}
    assert "actions/upgrade/actions/defaults.yml" not in paths


# ---------------------------------------------------------------------------
# root-only fallback
# ---------------------------------------------------------------------------


def test_scan_root_only_pack_synthesizes_root_finding():
    result = scan_repository(FIXTURES_ROOT / "root_only", repo_name="rootlike")

    assert len(result.packs) == 1
    pack = result.packs[0]
    assert pack.path == ""  # synthetic root pack
    assert pack.name == "rootlike"  # falls back to repo_name
    assert pack.contributed_keys == ("echo-test",)
    assert pack.pack_yml_present is False
    assert pack.errors == ()

    assert result.gitops_files == []


def test_scan_root_only_without_repo_name_uses_default():
    result = scan_repository(FIXTURES_ROOT / "root_only")
    assert result.packs[0].name == "pack"


# ---------------------------------------------------------------------------
# empty / nonexistent
# ---------------------------------------------------------------------------


def test_scan_empty_directory_returns_empty_result(tmp_path: Path):
    result = scan_repository(tmp_path)
    assert result.packs == []
    assert result.gitops_files == []
    assert result.scan_errors == []


def test_scan_nonexistent_path_returns_scan_error(tmp_path: Path):
    nonexistent = tmp_path / "does-not-exist"
    result = scan_repository(nonexistent)
    assert result.packs == []
    assert result.gitops_files == []
    assert len(result.scan_errors) == 1
    assert "not a directory" in result.scan_errors[0].message


# ---------------------------------------------------------------------------
# broken manifests + gitops
# ---------------------------------------------------------------------------


def test_scan_broken_manifest_yields_error_findings_no_raise():
    """Two broken manifests in the bad/ pack: one fails YAML parse, the
    other parses but fails ActionManifest validation. Both must be
    reported as errors on the DetectedPack without aborting the scan."""
    result = scan_repository(FIXTURES_ROOT / "broken")

    assert len(result.packs) == 1
    pack = result.packs[0]
    assert pack.name == "bad-pack"
    assert pack.path == "actions/bad"

    # The schema-invalid manifest still surfaces its key (best-effort
    # extraction so the conflict resolver sees what it claimed); the
    # YAML-broken one cannot extract a key.
    assert pack.contributed_keys == ("missing-required-fields",)
    assert len(pack.errors) == 2
    error_files = sorted(e.file for e in pack.errors)
    assert error_files == [
        "actions/bad/actions/missing-required-fields/manifest.yml",
        "actions/bad/actions/yaml-syntax-error/manifest.yml",
    ]


def test_scan_invalid_gitops_yaml_extracts_group_name_with_errors():
    """A gitops YAML with valid ``group:`` but a body that fails
    LabDogGroupYAML must still surface as a finding with the group_name
    populated and the validation error attached."""
    result = scan_repository(FIXTURES_ROOT / "broken")
    by_path = {g.path: g for g in result.gitops_files}
    bad = by_path.get("groups/bad-group.yaml")
    assert bad is not None
    assert bad.group_name == "bad-group"
    assert len(bad.errors) >= 1
    assert any("validation" in e.message.lower() for e in bad.errors)


# ---------------------------------------------------------------------------
# edge cases via tmp_path
# ---------------------------------------------------------------------------


def test_scan_pack_yml_with_yaml_syntax_error_records_finding(tmp_path: Path):
    """A directory with a malformed pack.yml is still treated as a pack
    root; the parse error is captured as a ScanError on the pack."""
    pack_dir = tmp_path / "broken-pack"
    pack_dir.mkdir()
    (pack_dir / "pack.yml").write_text("name: [unclosed\n")

    result = scan_repository(tmp_path)

    assert len(result.packs) == 1
    pack = result.packs[0]
    assert pack.name == "broken-pack"  # falls back to dirname
    assert pack.pack_yml_present is True
    # A pack.yml with broken YAML must produce *some* error finding;
    # the exact PyYAML message wording isn't part of our contract.
    assert len(pack.errors) >= 1
    assert pack.errors[0].file == "broken-pack/pack.yml"


def test_scan_pack_yml_without_name_uses_dirname(tmp_path: Path):
    pack_dir = tmp_path / "anonymous-pack"
    pack_dir.mkdir()
    (pack_dir / "pack.yml").write_text("description: no name field\n")

    result = scan_repository(tmp_path)
    assert result.packs[0].name == "anonymous-pack"


def test_scan_yaml_with_no_top_level_dict_skipped(tmp_path: Path):
    """A YAML file containing a list at the top level is not a gitops
    candidate — it's just some other YAML in the repo."""
    (tmp_path / "list.yaml").write_text("- one\n- two\n")
    result = scan_repository(tmp_path)
    assert result.gitops_files == []


def test_scan_yaml_parse_error_on_yaml_file_yields_finding(tmp_path: Path):
    """A ``.yaml`` file that fails YAML parsing is surfaced as a finding
    with no group_name and a parse-error ScanError."""
    (tmp_path / "broken.yaml").write_text("group: web\n  bad: indent\n   worse: indent\n")
    result = scan_repository(tmp_path)
    assert len(result.gitops_files) == 1
    finding = result.gitops_files[0]
    assert finding.path == "broken.yaml"
    assert finding.group_name is None
    assert any("parse" in e.message.lower() for e in finding.errors)


# ---------------------------------------------------------------------------
# dataclass shape sanity
# ---------------------------------------------------------------------------


def test_dataclasses_are_frozen():
    """Findings must be immutable so callers can pass them around safely."""
    pack = DetectedPack(path="x", name="x", contributed_keys=(), pack_yml_present=False)
    gitops = DetectedGitopsFile(path="x", group_name=None)
    err = ScanError(file="x", message="x")
    for obj in (pack, gitops, err):
        try:
            obj.path = "mutated"  # type: ignore[misc]
        except Exception:
            pass
        else:
            raise AssertionError(f"{type(obj).__name__} should be frozen")
