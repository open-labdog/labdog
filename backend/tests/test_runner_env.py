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
