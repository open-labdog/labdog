"""SEC-46: ansible-playbook must not inherit LabDog's secrets."""

import json
import os

from app.ansible_runtime.runner import run_ansible, scrub_child_env


def test_scrub_keeps_what_the_run_needs():
    inherited = {
        "PATH": "/usr/bin",
        "LABDOG_SECURITY__SECRET_KEY": "s",
        "LABDOG_DATABASE__URL": "postgresql://x",
        "SOME_OTHER_SECRET": "y",
        "ANSIBLE_CONFIG": "/etc/ansible.cfg",
    }
    env = {**inherited, "ANSIBLE_ROLES_PATH": "/r", "AWX_ISOLATED_DATA_DIR": "/a"}
    out = scrub_child_env(env, inherited)
    assert out == {
        "PATH": "/usr/bin",
        "ANSIBLE_CONFIG": "/etc/ansible.cfg",
        "ANSIBLE_ROLES_PATH": "/r",
        "AWX_ISOLATED_DATA_DIR": "/a",
    }


def test_a_real_playbook_sees_no_labdog_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("LABDOG_SECURITY__ENCRYPTION_KEY", "do-not-leak")
    out_file = tmp_path / "env.json"
    playbook = (
        "- hosts: all\n  gather_facts: false\n  tasks:\n"
        "    - ansible.builtin.copy:\n"
        f"        dest: {out_file}\n"
        "        content: \"{{ lookup('ansible.builtin.env', 'LABDOG_SECURITY__ENCRYPTION_KEY',"
        " default='absent') | to_json }}\"\n"
    )
    inventory = json.dumps({"all": {"hosts": {"localhost": {"ansible_connection": "local"}}}})
    runner = run_ansible(
        playbook_yaml=playbook, inventory_json=inventory, private_data_dir=str(tmp_path / "run")
    )
    assert runner.status == "successful", runner.stdout.read()
    assert json.loads(out_file.read_text()) == "absent"
    assert os.environ["LABDOG_SECURITY__ENCRYPTION_KEY"] == "do-not-leak"
    artifacts = list((tmp_path / "run" / "artifacts").rglob("command"))
    assert artifacts and "do-not-leak" not in artifacts[0].read_text()


def _effective_pipelining(tmp_path, **kwargs) -> bool:
    """Run a real playbook and return the ANSIBLE_PIPELINING it ran with."""
    out_file = tmp_path / "pipelining.json"
    lookup = "lookup('ansible.builtin.config', 'ANSIBLE_PIPELINING') | to_json"
    playbook = (
        "- hosts: all\n  gather_facts: false\n  tasks:\n"
        "    - ansible.builtin.copy:\n"
        f"        dest: {out_file}\n"
        f'        content: "{{{{ {lookup} }}}}"\n'
    )
    inventory = json.dumps({"all": {"hosts": {"localhost": {"ansible_connection": "local"}}}})
    runner = run_ansible(
        playbook_yaml=playbook,
        inventory_json=inventory,
        private_data_dir=str(tmp_path / "run"),
        **kwargs,
    )
    assert runner.status == "successful", runner.stdout.read()
    return json.loads(out_file.read_text())


def test_a_real_playbook_runs_with_pipelining(tmp_path):
    """BUG-154: every task paid for a temp dir, an SFTP copy, a chmod and a
    cleanup before it. Pipelining sends the module down the open session."""
    assert _effective_pipelining(tmp_path) is True


def test_a_caller_can_turn_pipelining_off(tmp_path):
    assert _effective_pipelining(tmp_path, envvars={"ANSIBLE_PIPELINING": "False"}) is False


def test_a_run_prints_nothing_but_keeps_its_output(tmp_path, capfd):
    """BUG-176: ansible-runner echoed every playbook line to stdout, which
    Celery logs at WARNING. The events and the artifact keep it all."""
    events: list[dict] = []
    playbook = (
        "- hosts: all\n  gather_facts: false\n  tasks:\n"
        "    - ansible.builtin.debug:\n        msg: labdog-bug-176\n"
    )
    inventory = json.dumps({"all": {"hosts": {"localhost": {"ansible_connection": "local"}}}})
    runner = run_ansible(
        playbook_yaml=playbook,
        inventory_json=inventory,
        private_data_dir=str(tmp_path / "run"),
        event_handler=lambda e: events.append(e) or True,
    )

    assert runner.status == "successful"
    out, _ = capfd.readouterr()
    assert "labdog-bug-176" not in out
    assert "labdog-bug-176" in runner.stdout.read()
    assert any("labdog-bug-176" in (e.get("stdout") or "") for e in events)
