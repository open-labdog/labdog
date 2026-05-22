"""
Unit tests for BUG-48 (ip_address validation) and BUG-49 (ssh_user allow-list)
on HostCreate / HostUpdate and SSHKeyCreate / SSHKeyUpdate schemas.
"""

import pytest
from pydantic import ValidationError

from app.schemas.hosts import HostCreate, HostUpdate
from app.schemas.ssh_keys import SSHKeyCreate, SSHKeyUpdate

# ---------------------------------------------------------------------------
# BUG-48 — HostCreate.ip_address validation
# ---------------------------------------------------------------------------


class TestHostCreateIpAddress:
    # --- valid addresses accepted ---

    def test_valid_private_ipv4(self):
        h = HostCreate(ip_address="192.168.1.10")
        assert h.ip_address == "192.168.1.10"

    def test_valid_rfc1918_10_block(self):
        h = HostCreate(ip_address="10.0.0.5")
        assert h.ip_address == "10.0.0.5"

    def test_valid_documentation_ipv6(self):
        h = HostCreate(ip_address="2001:db8::1")
        assert h.ip_address == "2001:db8::1"

    def test_valid_public_ipv4(self):
        h = HostCreate(ip_address="203.0.113.5")
        assert h.ip_address == "203.0.113.5"

    # --- invalid / special-use addresses rejected ---

    def test_hostname_string_rejected(self):
        with pytest.raises(ValidationError, match="not a valid IPv4 or IPv6 literal"):
            HostCreate(ip_address="localhost")

    def test_loopback_127_0_0_1_rejected(self):
        with pytest.raises(ValidationError, match="loopback"):
            HostCreate(ip_address="127.0.0.1")

    def test_loopback_127_0_0_5_rejected(self):
        # 127.0.0.0/8 is the full loopback block, not just .1
        with pytest.raises(ValidationError, match="loopback"):
            HostCreate(ip_address="127.0.0.5")

    def test_link_local_cloud_metadata_rejected(self):
        with pytest.raises(ValidationError, match="link-local"):
            HostCreate(ip_address="169.254.169.254")

    def test_ipv6_loopback_rejected(self):
        with pytest.raises(ValidationError, match="loopback"):
            HostCreate(ip_address="::1")

    def test_unspecified_ipv4_rejected(self):
        with pytest.raises(ValidationError, match="unspecified"):
            HostCreate(ip_address="0.0.0.0")

    def test_unspecified_ipv6_rejected(self):
        with pytest.raises(ValidationError, match="unspecified"):
            HostCreate(ip_address="::")

    def test_garbage_string_rejected(self):
        with pytest.raises(ValidationError, match="not a valid IPv4 or IPv6 literal"):
            HostCreate(ip_address="not-an-ip")

    def test_empty_string_rejected(self):
        with pytest.raises(ValidationError):
            HostCreate(ip_address="")


# ---------------------------------------------------------------------------
# BUG-48 — HostUpdate.ip_address validation
# ---------------------------------------------------------------------------


class TestHostUpdateIpAddress:
    def test_none_accepted(self):
        h = HostUpdate(ip_address=None)
        assert h.ip_address is None

    def test_valid_ip_accepted(self):
        h = HostUpdate(ip_address="10.10.10.10")
        assert h.ip_address == "10.10.10.10"

    def test_loopback_rejected(self):
        with pytest.raises(ValidationError, match="loopback"):
            HostUpdate(ip_address="127.0.0.1")

    def test_garbage_string_rejected(self):
        with pytest.raises(ValidationError, match="not a valid IPv4 or IPv6 literal"):
            HostUpdate(ip_address="not-an-ip")


# ---------------------------------------------------------------------------
# BUG-49 — HostCreate.ssh_user allow-list
# ---------------------------------------------------------------------------


class TestHostCreateSshUser:
    # --- valid users accepted ---

    def test_root_accepted(self):
        h = HostCreate(ip_address="10.0.0.1", ssh_user="root")
        assert h.ssh_user == "root"

    def test_deploy_accepted(self):
        h = HostCreate(ip_address="10.0.0.1", ssh_user="deploy")
        assert h.ssh_user == "deploy"

    def test_hyphenated_accepted(self):
        h = HostCreate(ip_address="10.0.0.1", ssh_user="my-user")
        assert h.ssh_user == "my-user"

    def test_leading_underscore_accepted(self):
        h = HostCreate(ip_address="10.0.0.1", ssh_user="_systemd")
        assert h.ssh_user == "_systemd"

    # --- invalid users rejected ---

    def test_space_rejected(self):
        with pytest.raises(ValidationError, match="not a valid Linux username"):
            HostCreate(ip_address="10.0.0.1", ssh_user="with space")

    def test_newline_rejected(self):
        with pytest.raises(ValidationError, match="not a valid Linux username"):
            HostCreate(ip_address="10.0.0.1", ssh_user="with\nnewline")

    def test_semicolon_rejected(self):
        with pytest.raises(ValidationError, match="not a valid Linux username"):
            HostCreate(ip_address="10.0.0.1", ssh_user="with;semicolon")

    def test_leading_dash_rejected(self):
        with pytest.raises(ValidationError, match="not a valid Linux username"):
            HostCreate(ip_address="10.0.0.1", ssh_user="-leadingdash")

    def test_uppercase_rejected(self):
        with pytest.raises(ValidationError, match="not a valid Linux username"):
            HostCreate(ip_address="10.0.0.1", ssh_user="User")

    def test_empty_string_rejected(self):
        with pytest.raises(ValidationError, match="not a valid Linux username"):
            HostCreate(ip_address="10.0.0.1", ssh_user="")

    def test_33_char_string_rejected(self):
        with pytest.raises(ValidationError, match="not a valid Linux username"):
            HostCreate(ip_address="10.0.0.1", ssh_user="a" * 33)


# ---------------------------------------------------------------------------
# BUG-49 — HostUpdate.ssh_user allow-list
# ---------------------------------------------------------------------------


class TestHostUpdateSshUser:
    def test_none_accepted(self):
        h = HostUpdate(ssh_user=None)
        assert h.ssh_user is None

    def test_valid_user_accepted(self):
        h = HostUpdate(ssh_user="deploy")
        assert h.ssh_user == "deploy"

    def test_invalid_user_rejected(self):
        with pytest.raises(ValidationError, match="not a valid Linux username"):
            HostUpdate(ssh_user="with space")


# ---------------------------------------------------------------------------
# BUG-49 — SSHKeyCreate.ssh_user allow-list
# ---------------------------------------------------------------------------


class TestSSHKeyCreateSshUser:
    # --- valid users accepted ---

    def test_root_default_accepted(self):
        key = SSHKeyCreate(name="k", private_key="dummy", ssh_user="root")
        assert key.ssh_user == "root"

    def test_deploy_accepted(self):
        key = SSHKeyCreate(name="k", private_key="dummy", ssh_user="deploy")
        assert key.ssh_user == "deploy"

    def test_hyphenated_accepted(self):
        key = SSHKeyCreate(name="k", private_key="dummy", ssh_user="my-user")
        assert key.ssh_user == "my-user"

    def test_leading_underscore_accepted(self):
        key = SSHKeyCreate(name="k", private_key="dummy", ssh_user="_systemd")
        assert key.ssh_user == "_systemd"

    # --- invalid users rejected ---

    def test_space_rejected(self):
        with pytest.raises(ValidationError, match="not a valid Linux username"):
            SSHKeyCreate(name="k", private_key="dummy", ssh_user="with space")

    def test_newline_rejected(self):
        with pytest.raises(ValidationError, match="not a valid Linux username"):
            SSHKeyCreate(name="k", private_key="dummy", ssh_user="with\nnewline")

    def test_semicolon_rejected(self):
        with pytest.raises(ValidationError, match="not a valid Linux username"):
            SSHKeyCreate(name="k", private_key="dummy", ssh_user="with;semicolon")

    def test_leading_dash_rejected(self):
        with pytest.raises(ValidationError, match="not a valid Linux username"):
            SSHKeyCreate(name="k", private_key="dummy", ssh_user="-leadingdash")

    def test_uppercase_rejected(self):
        with pytest.raises(ValidationError, match="not a valid Linux username"):
            SSHKeyCreate(name="k", private_key="dummy", ssh_user="User")

    def test_empty_string_rejected(self):
        with pytest.raises(ValidationError, match="not a valid Linux username"):
            SSHKeyCreate(name="k", private_key="dummy", ssh_user="")

    def test_33_char_string_rejected(self):
        with pytest.raises(ValidationError, match="not a valid Linux username"):
            SSHKeyCreate(name="k", private_key="dummy", ssh_user="a" * 33)


# ---------------------------------------------------------------------------
# BUG-49 — SSHKeyUpdate.ssh_user allow-list
# ---------------------------------------------------------------------------


class TestSSHKeyUpdateSshUser:
    def test_none_accepted(self):
        key = SSHKeyUpdate(ssh_user=None)
        assert key.ssh_user is None

    def test_valid_user_accepted(self):
        key = SSHKeyUpdate(ssh_user="git")
        assert key.ssh_user == "git"

    def test_invalid_user_rejected(self):
        with pytest.raises(ValidationError, match="not a valid Linux username"):
            SSHKeyUpdate(ssh_user="with space")
