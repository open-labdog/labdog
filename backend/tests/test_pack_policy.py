"""SEC-21: a pack may not ship controller-side code unless it is trusted.

An action pack is a git repository someone points LabDog at. Its playbooks
are meant to run against managed hosts — but ansible-core gives a repository
several ways to run code on the *controller*, which is the LabDog host, as
the labdog user: plugin directories it imports Python from, and plays that
target localhost or set ``connection: local``.

The loader validated the manifest's parameter declarations and never looked
at the playbook body, so registering a pack was equivalent to handing over
code execution on the LabDog host. Under the flat privilege model that is
reachable by any authenticated account, with nothing behind it.

``trusted`` is not a privilege boundary and these tests do not pretend it is
— any user can set it. What it buys is that accepting such content is a
deliberate, audited act rather than a side effect of adding a repository.
"""

from pathlib import Path

import pytest

from app.actions.pack_policy import (
    UntrustedPackContent,
    assert_pack_safe,
    inspect_pack,
)
from app.actions.packs import Pack, load_pack

SIMPLE_MANIFEST = """\
key: demo
name: Demo action
description: A demo.
icon: Box
playbook: playbook.yml
version: "1.0"
estimated_duration: "1 min"
"""
REMOTE_PLAYBOOK = "---\n- name: demo\n  hosts: all\n  tasks: []\n"


def _pack(root: Path, name: str, *, playbook: str = REMOTE_PLAYBOOK, extra: dict | None = None):
    """Write a minimal, valid pack, plus any extra files."""
    d = root / name / "actions" / "demo"
    d.mkdir(parents=True)
    (d / "manifest.yml").write_text(SIMPLE_MANIFEST)
    (d / "playbook.yml").write_text(playbook)
    for rel, body in (extra or {}).items():
        target = root / name / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    return root / name


class TestControllerSidePythonIsFound:
    @pytest.mark.parametrize(
        "plugin_dir",
        [
            "action_plugins",
            "library",
            "filter_plugins",
            "lookup_plugins",
            "callback_plugins",
            "module_utils",
            "strategy_plugins",
        ],
    )
    def test_a_plugin_directory_anywhere_is_a_finding(self, tmp_path: Path, plugin_dir):
        root = _pack(tmp_path, "p", extra={f"{plugin_dir}/evil.py": "import os\n"})
        findings = inspect_pack(root)
        assert any("controller-side Python" in f.kind for f in findings)

    def test_a_plugin_directory_nested_in_a_role_is_found(self, tmp_path: Path):
        """ANSIBLE_ROLES_PATH puts pack roles on the search path, so a
        plugin dir inside a role is as live as one at the top level."""
        root = _pack(tmp_path, "p", extra={"roles/r/library/evil.py": "import os\n"})
        assert inspect_pack(root)


class TestLocalExecutionPlaysAreFound:
    @pytest.mark.parametrize(
        "playbook",
        [
            "---\n- name: x\n  hosts: localhost\n  tasks: []\n",
            "---\n- name: x\n  hosts: 127.0.0.1\n  tasks: []\n",
            "---\n- name: x\n  hosts: all\n  connection: local\n  tasks: []\n",
            "---\n- name: x\n  hosts: [localhost]\n  tasks: []\n",
        ],
    )
    def test_a_play_targeting_the_controller_is_a_finding(self, tmp_path: Path, playbook):
        root = _pack(tmp_path, "p", playbook=playbook)
        findings = inspect_pack(root)
        assert any("runs on the controller" in f.kind for f in findings)

    def test_delegate_to_localhost_in_a_task_is_a_finding(self, tmp_path: Path):
        pb = (
            "---\n- name: x\n  hosts: all\n  tasks:\n"
            "    - name: sneaky\n      ansible.builtin.shell: id\n"
            "      delegate_to: localhost\n"
        )
        root = _pack(tmp_path, "p", playbook=pb)
        assert any("runs on the controller" in f.kind for f in inspect_pack(root))

    def test_delegate_to_inside_a_nested_block_is_a_finding(self, tmp_path: Path):
        """Blocks nest, so a flat scan of `tasks` would miss this."""
        pb = (
            "---\n- name: x\n  hosts: all\n  tasks:\n"
            "    - block:\n"
            "        - name: sneaky\n          ansible.builtin.shell: id\n"
            "          delegate_to: localhost\n"
        )
        root = _pack(tmp_path, "p", playbook=pb)
        assert any("runs on the controller" in f.kind for f in inspect_pack(root))


class TestOrdinaryPacksAreNotFlagged:
    """The regression half. A check that refused normal packs would be
    turned off, and then it protects nothing."""

    def test_a_plain_remote_pack_is_clean(self, tmp_path: Path):
        assert inspect_pack(_pack(tmp_path, "p")) == []

    def test_roles_and_verify_playbooks_are_clean(self, tmp_path: Path):
        root = _pack(
            tmp_path,
            "p",
            extra={
                "roles/r/tasks/main.yml": "---\n[]\n",
                "actions/demo/checks/verify.yml": REMOTE_PLAYBOOK,
            },
        )
        assert inspect_pack(root) == []

    def test_an_unparseable_yaml_file_is_not_treated_as_hostile(self, tmp_path: Path):
        """Unreadable is not the same as dangerous — the loader reports a
        broken playbook on its own; this check declines to guess."""
        root = _pack(tmp_path, "p", extra={"broken.yml": "{{{ not yaml"})
        assert inspect_pack(root) == []


class TestTheGate:
    def test_untrusted_content_raises_and_names_what_it_found(self, tmp_path: Path):
        root = _pack(tmp_path, "p", extra={"library/evil.py": "import os\n"})
        with pytest.raises(UntrustedPackContent) as exc:
            assert_pack_safe("p", root, trusted=False)
        message = str(exc.value)
        assert "library" in message
        assert "trusted" in message, "the refusal has to say what to do about it"

    def test_trusted_content_is_allowed(self, tmp_path: Path):
        root = _pack(tmp_path, "p", extra={"library/evil.py": "import os\n"})
        assert_pack_safe("p", root, trusted=True) is None

    def test_a_clean_pack_needs_no_trust(self, tmp_path: Path):
        assert_pack_safe("p", _pack(tmp_path, "p"), trusted=False) is None


class TestTheLoaderEnforcesIt:
    def test_an_untrusted_pack_contributes_no_actions(self, tmp_path: Path):
        root = _pack(tmp_path, "p", extra={"action_plugins/evil.py": "import os\n"})
        assert load_pack(Pack(name="p", path=root, trusted=False)) == []

    def test_the_same_pack_loads_once_trusted(self, tmp_path: Path):
        root = _pack(tmp_path, "p", extra={"action_plugins/evil.py": "import os\n"})
        defns = load_pack(Pack(name="p", path=root, trusted=True))
        assert [d.key for d in defns] == ["demo"]

    def test_an_ordinary_pack_loads_untrusted(self, tmp_path: Path):
        defns = load_pack(Pack(name="p", path=_pack(tmp_path, "p"), trusted=False))
        assert [d.key for d in defns] == ["demo"]

    def test_pack_defaults_to_untrusted(self):
        """A caller that forgets the flag gets the safe answer."""
        assert Pack(name="p", path=Path("/nonexistent")).trusted is False
