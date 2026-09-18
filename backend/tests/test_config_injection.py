"""SEC-24/SEC-25: a value LabDog writes into a config file must not end the line.

Two fields were interpolated into generated files with no check that they
could not close the line they sat on:

* ``HostsEntryCreate.comment`` → ``<ip> <host> <aliases>  # <comment>`` in
  ``/etc/hosts``. ``hostname`` and ``aliases`` were validated against
  HOSTNAME_RE; ``comment`` was validated not at all. A comment of
  ``"x\\n1.2.3.4 deb.debian.org"`` appended a working entry — a
  package-mirror redirect on every host in the group, written by LabDog
  itself, and invisible in a UI that renders the comment on one line.
* ``sudo_rule`` → ``{username} {sudo_rule}\\n`` in
  ``/etc/sudoers.d/{username}``. The shell metacharacters were blocked;
  ``\\n`` was not. ``visudo -cf`` validates the result and accepts it,
  because a two-line drop-in granting passwordless root to an unmanaged
  account is perfectly valid sudoers syntax.

``Host.hostname`` is the third door to the first file — the hosts-file
merge renders referenced hosts into ``/etc/hosts`` — and had no validator
of any kind.

Each is checked at the schema *and* at the point of rendering. Entries
also arrive through the GitOps YAML importer, and rows written before
these validators existed are still in the database, so the schema alone
would leave the file-writing code trusting its input.
"""

import pytest
from pydantic import ValidationError

from app.hosts_mgmt.schemas import HostsEntryCreate, HostsEntryUpdate
from app.schemas.hosts import HostCreate, HostUpdate
from app.user_mgmt.schemas import LinuxUserCreate, LinuxUserUpdate

LINE_ENDERS = ["\n", "\r", "\r\n", "\x00"]


class TestAnEtcHostsCommentCannotStartANewLine:
    @pytest.mark.parametrize("ender", LINE_ENDERS)
    def test_a_line_ender_in_a_comment_is_refused(self, ender):
        with pytest.raises(ValidationError):
            HostsEntryCreate(
                ip_address="10.0.0.1",
                hostname="web",
                comment=f"note{ender}1.2.3.4 deb.debian.org",
            )

    @pytest.mark.parametrize("ender", LINE_ENDERS)
    def test_the_update_schema_refuses_it_too(self, ender):
        """The original report was against the *Add* dialog; edit reaches
        the same file."""
        with pytest.raises(ValidationError):
            HostsEntryUpdate(comment=f"note{ender}1.2.3.4 evil.example")

    def test_an_ordinary_comment_is_accepted(self):
        entry = HostsEntryCreate(
            ip_address="10.0.0.1", hostname="web", comment="prod web tier (ticket #42)"
        )
        assert entry.comment == "prod web tier (ticket #42)"

    def test_an_absurdly_long_comment_is_refused(self):
        with pytest.raises(ValidationError):
            HostsEntryCreate(ip_address="10.0.0.1", hostname="web", comment="x" * 500)

    def test_no_comment_is_still_fine(self):
        assert HostsEntryCreate(ip_address="10.0.0.1", hostname="web").comment is None


class TestTheRenderedFileIsSafeRegardless:
    """The schema is the first line; this is the point where a newline
    would become a real /etc/hosts entry."""

    def test_a_comment_that_reached_the_database_anyway_is_neutralised(self):
        from app.hosts_mgmt.merge import _SAFE_COMMENT

        class _Entry:
            ip_address = "10.0.0.1"
            hostname = "web"
            aliases: list[str] = []
            comment = "note\n1.2.3.4 deb.debian.org"

        rendered = f"  # {_SAFE_COMMENT.sub(' ', _Entry.comment)}"
        assert "\n" not in rendered
        assert "deb.debian.org" in rendered, "the text is kept, only the line break goes"


class TestAManagedHostnameIsValidated:
    """The other door to /etc/hosts: referenced hosts are rendered into it."""

    @pytest.mark.parametrize("ender", LINE_ENDERS)
    def test_a_line_ender_in_a_hostname_is_refused(self, ender):
        with pytest.raises(ValidationError):
            HostCreate(hostname=f"web{ender}1.2.3.4 evil.example", ip_address="10.0.0.1")

    def test_the_update_schema_refuses_it_too(self):
        with pytest.raises(ValidationError):
            HostUpdate(hostname="web\n1.2.3.4 evil.example")

    def test_an_ordinary_hostname_is_accepted(self):
        assert HostCreate(hostname="web-01.example.com", ip_address="10.0.0.1").hostname == (
            "web-01.example.com"
        )

    def test_no_hostname_is_still_fine(self):
        """Discovery adds hosts by IP before a name is known."""
        assert HostCreate(ip_address="10.0.0.1").hostname is None


class TestSshPortIsAPort:
    @pytest.mark.parametrize("port", [0, -1, 65536, 999999])
    def test_an_impossible_port_is_refused(self, port):
        with pytest.raises(ValidationError):
            HostCreate(ip_address="10.0.0.1", ssh_port=port)

    def test_a_real_port_is_accepted(self):
        assert HostCreate(ip_address="10.0.0.1", ssh_port=2222).ssh_port == 2222

    def test_the_update_schema_bounds_it_too(self):
        with pytest.raises(ValidationError):
            HostUpdate(ssh_port=70000)


class TestASudoRuleCannotBecomeTwoRules:
    @pytest.mark.parametrize("ender", ["\n", "\r", "\x00"])
    def test_a_line_ender_in_a_sudo_rule_is_refused(self, ender):
        with pytest.raises(ValidationError):
            LinuxUserCreate(
                username="deploy",
                sudo_rule=f"NOPASSWD: /bin/true{ender}evil ALL=ALL",
            )

    @pytest.mark.parametrize("ender", ["\n", "\r", "\x00"])
    def test_the_update_schema_refuses_it_too(self, ender):
        """The validator is duplicated on create and update; both copies
        needed the newline."""
        with pytest.raises(ValidationError):
            LinuxUserUpdate(sudo_rule=f"NOPASSWD: /bin/true{ender}evil ALL=ALL")

    def test_the_shell_metacharacters_are_still_refused(self):
        """The original rule set must not regress while adding to it."""
        with pytest.raises(ValidationError):
            LinuxUserCreate(username="deploy", sudo_rule="NOPASSWD: /bin/`id`")

    def test_an_ordinary_rule_is_accepted(self):
        # Parentheses are on the pre-existing forbidden list, so a real
        # accepted rule looks like this rather than the `ALL=(ALL)` form.
        rule = LinuxUserCreate(
            username="deploy", sudo_rule="/usr/bin/apt, /usr/bin/systemctl restart nginx"
        )
        assert rule.sudo_rule == "/usr/bin/apt, /usr/bin/systemctl restart nginx"


class TestTheSudoersFileIsSafeRegardless:
    """`visudo -cf` cannot catch this: two lines granting root is valid
    sudoers. The check has to happen before the file is written."""

    def test_the_generator_refuses_a_multiline_rule(self):
        from app.user_mgmt.generator import _sudoers_tasks

        with pytest.raises(ValueError, match="forbidden characters"):
            _sudoers_tasks(
                [
                    {
                        "username": "deploy",
                        "state": "present",
                        "sudo_rule": "NOPASSWD: /bin/true\nevil ALL=ALL",
                    }
                ]
            )

    def test_the_generator_still_writes_an_ordinary_rule(self):
        from app.user_mgmt.generator import _sudoers_tasks

        tasks = _sudoers_tasks(
            [
                {
                    "username": "deploy",
                    "state": "present",
                    "sudo_rule": "/usr/bin/systemctl restart nginx",
                }
            ]
        )
        content = tasks[0]["ansible.builtin.copy"]["content"]
        assert content == "deploy /usr/bin/systemctl restart nginx\n"
        assert content.count("\n") == 1, "a sudoers drop-in LabDog writes is one line"

    def test_removal_is_unaffected(self):
        from app.user_mgmt.generator import _sudoers_tasks

        tasks = _sudoers_tasks([{"username": "deploy", "state": "absent"}])
        assert tasks[0]["ansible.builtin.file"]["state"] == "absent"
