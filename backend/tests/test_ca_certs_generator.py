"""Unit tests for the CA cert Ansible playbook generator."""

from app.ca_certs.generator import (
    cert_filename,
    generate_ca_cert_playbook,
)

PEM_FAKE_A = "-----BEGIN CERTIFICATE-----\nFAKE_A\n-----END CERTIFICATE-----\n"
PEM_FAKE_B = "-----BEGIN CERTIFICATE-----\nFAKE_B\n-----END CERTIFICATE-----\n"

FP_A = "AB:CD:EF:" + ":".join(["00"] * 29)
FP_B = "12:34:56:" + ":".join(["FF"] * 29)


def _present(name: str, fp: str, pem: str) -> dict:
    return {
        "name": name,
        "fingerprint_sha256": fp,
        "pem_content": pem,
        "state": "present",
        "subject": None,
        "issuer": None,
        "not_before": None,
        "not_after": None,
        "source": "group",
        "source_id": 1,
        "source_name": "g",
    }


def _absent(name: str, fp: str) -> dict:
    return {
        "name": name,
        "fingerprint_sha256": fp,
        "pem_content": "",
        "state": "absent",
        "subject": None,
        "issuer": None,
        "not_before": None,
        "not_after": None,
        "source": "host",
        "source_id": 1,
        "source_name": "host override",
    }


class TestCertFilename:
    def test_filename_format(self):
        assert cert_filename(FP_A) == "labdog-abcdef0000000000.crt"

    def test_filename_strips_colons_and_lowercases(self):
        assert cert_filename(FP_B) == "labdog-123456ffffffffff.crt"


class TestGenerateCACertPlaybook:
    def _gen(self, certs):
        return generate_ca_cert_playbook(
            host_ip="10.0.0.1",
            certs=certs,
            ssh_key_path="/dev/shm/key",
            ssh_port=22,
            ssh_user="root",
        )

    def test_empty_cert_list_still_emits_reconcile(self):
        result = self._gen([])
        playbook = result["playbook"]
        assert len(playbook) == 1
        play = playbook[0]
        assert play["become"] is True
        assert play["gather_facts"] is True
        # Should still have find + reconcile tasks for each OS family
        # (so an empty deploy = "remove all LabDog-managed certs")
        find_tasks = [t for t in play["tasks"] if "ansible.builtin.find" in t]
        assert len(find_tasks) == 3  # Debian, RedHat, Suse

    def test_single_present_cert_emits_copy_per_os(self):
        certs = [_present("Acme Root", FP_A, PEM_FAKE_A)]
        play = self._gen(certs)["playbook"][0]

        copy_tasks = [t for t in play["tasks"] if "ansible.builtin.copy" in t]
        # 3 OS families × 1 cert
        assert len(copy_tasks) == 3

        for task in copy_tasks:
            copy = task["ansible.builtin.copy"]
            assert copy["content"] == PEM_FAKE_A
            assert copy["mode"] == "0644"
            assert copy["owner"] == "root"
            assert "labdog-abcdef0000000000.crt" in copy["dest"]

    def test_os_family_when_clauses(self):
        play = self._gen([_present("X", FP_A, PEM_FAKE_A)])["playbook"][0]
        os_families = set()
        for task in play["tasks"]:
            when = task.get("when", "")
            if "Debian" in when:
                os_families.add("Debian")
            if "RedHat" in when:
                os_families.add("RedHat")
            if "Suse" in when:
                os_families.add("Suse")
        assert os_families == {"Debian", "RedHat", "Suse"}

    def test_correct_drop_in_directories(self):
        play = self._gen([_present("X", FP_A, PEM_FAKE_A)])["playbook"][0]
        copy_tasks = [t for t in play["tasks"] if "ansible.builtin.copy" in t]
        dests = [t["ansible.builtin.copy"]["dest"] for t in copy_tasks]
        assert any("/usr/local/share/ca-certificates/" in d for d in dests)
        assert any("/etc/pki/ca-trust/source/anchors/" in d for d in dests)
        assert any("/etc/pki/trust/anchors/" in d for d in dests)

    def test_absent_state_emits_explicit_remove(self):
        certs = [_absent("Removed CA", FP_B)]
        play = self._gen(certs)["playbook"][0]
        explicit_removes = [
            t
            for t in play["tasks"]
            if "ansible.builtin.file" in t
            and t["ansible.builtin.file"].get("state") == "absent"
            and "Remove CA cert (explicit absent)" in t.get("name", "")
        ]
        # 3 OS families × 1 absent cert
        assert len(explicit_removes) == 3
        for t in explicit_removes:
            assert "labdog-123456ffffffffff.crt" in t["ansible.builtin.file"]["path"]

    def test_handlers_present_for_each_os(self):
        play = self._gen([])["playbook"][0]
        handler_names = {h["name"] for h in play["handlers"]}
        assert handler_names == {
            "update-ca-debian",
            "update-ca-redhat",
            "update-ca-suse",
        }
        # Each handler runs a command
        for h in play["handlers"]:
            assert "ansible.builtin.command" in h

    def test_reconcile_loop_includes_keep_paths(self):
        certs = [
            _present("A", FP_A, PEM_FAKE_A),
            _present("B", FP_B, PEM_FAKE_B),
        ]
        play = self._gen(certs)["playbook"][0]
        reconcile_tasks = [t for t in play["tasks"] if "Remove orphaned" in t.get("name", "")]
        assert len(reconcile_tasks) == 3  # one per OS family
        for t in reconcile_tasks:
            loop_expr = t["loop"]
            # Both keep paths should be referenced in the rejectattr filter
            assert "labdog-abcdef0000000000.crt" in loop_expr
            assert "labdog-123456ffffffffff.crt" in loop_expr
            assert "rejectattr" in loop_expr

    def test_inventory_uses_provided_host(self):
        result = self._gen([])
        assert "10.0.0.1" in result["inventory"]
        assert "/dev/shm/key" in result["inventory"]

    def test_playbook_has_become_root(self):
        play = self._gen([])["playbook"][0]
        assert play["become"] is True
