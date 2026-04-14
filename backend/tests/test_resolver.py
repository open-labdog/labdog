import pytest
from pydantic import ValidationError

from app.resolver.schemas import ResolverConfigCreate
from app.resolver.renderer import (
    render_resolv_conf,
    render_systemd_resolved,
    render_networkmanager_conf,
)
from app.resolver.collector import (
    parse_resolv_conf,
    parse_resolvectl_output,
    parse_resolved_conf,
    parse_networkmanager_conf,
)
from app.resolver.diff import compute_resolver_diff, ResolverDiff
from app.resolver.generator import (
    CLOUD_INIT_DISABLE_PATH,
    generate_resolver_playbook,
)


# ---------------------------------------------------------------------------
# Schema validation tests (pure unit tests, no DB)
# ---------------------------------------------------------------------------


class TestResolverSchemas:
    def test_valid_ipv4_nameservers(self):
        """Accept valid IPv4 nameservers."""
        config = ResolverConfigCreate(nameservers=["8.8.8.8", "1.1.1.1"])
        assert len(config.nameservers) == 2
        assert config.nameservers == ["8.8.8.8", "1.1.1.1"]

    def test_valid_ipv6_nameservers(self):
        """Accept valid IPv6 nameservers."""
        config = ResolverConfigCreate(nameservers=["2606:4700:4700::1111"])
        assert config.nameservers[0] == "2606:4700:4700::1111"

    def test_empty_nameservers_rejected(self):
        """Reject empty nameservers list."""
        with pytest.raises(ValidationError, match="At least one nameserver"):
            ResolverConfigCreate(nameservers=[])

    def test_invalid_ip_rejected(self):
        """Reject invalid IP addresses."""
        with pytest.raises(ValidationError, match="Invalid IP address"):
            ResolverConfigCreate(nameservers=["not.an.ip"])

    def test_too_many_nameservers_rejected(self):
        """Reject more than 3 nameservers."""
        with pytest.raises(ValidationError, match="Maximum 3 nameservers"):
            ResolverConfigCreate(
                nameservers=["8.8.8.8", "8.8.4.4", "1.1.1.1", "1.0.0.1"]
            )

    def test_unknown_options_rejected(self):
        """Reject unknown options keys."""
        with pytest.raises(ValidationError, match="Unknown option"):
            ResolverConfigCreate(nameservers=["8.8.8.8"], options={"badkey": 1})

    def test_dns_over_tls_silently_disabled_for_resolv_conf(self):
        """dns_over_tls silently set to False for non-systemd-resolved."""
        config = ResolverConfigCreate(
            nameservers=["8.8.8.8"],
            resolver_type="resolv_conf",
            dns_over_tls=True,
        )
        assert config.dns_over_tls is False

    def test_dns_over_tls_allowed_for_systemd_resolved(self):
        """dns_over_tls stays True for systemd-resolved."""
        config = ResolverConfigCreate(
            nameservers=["8.8.8.8"],
            resolver_type="systemd_resolved",
            dns_over_tls=True,
        )
        assert config.dns_over_tls is True

    def test_defaults_applied(self):
        """Default values applied correctly."""
        config = ResolverConfigCreate(nameservers=["8.8.8.8"])
        assert config.search_domains == []
        assert config.options == {}
        assert config.resolver_type == "resolv_conf"
        assert config.dns_over_tls is False


# ---------------------------------------------------------------------------
# Renderer tests
# ---------------------------------------------------------------------------


class TestResolverRenderer:
    def test_render_resolv_conf(self):
        """Correct resolv.conf format."""
        result = render_resolv_conf(
            ["8.8.8.8", "1.1.1.1"], ["example.com"], {"ndots": 5}
        )
        assert "nameserver 8.8.8.8" in result
        assert "nameserver 1.1.1.1" in result
        assert "search example.com" in result
        assert "options ndots:5" in result

    def test_render_resolv_conf_boolean_options(self):
        """Boolean options (rotate, edns0) rendered as key-only."""
        result = render_resolv_conf(["8.8.8.8"], [], {"rotate": 1, "edns0": 1})
        assert "options rotate edns0" in result

    def test_render_systemd_resolved(self):
        """Correct systemd-resolved.conf INI format."""
        result = render_systemd_resolved(
            ["8.8.8.8"], ["example.com"], True
        )
        assert "[Resolve]" in result
        assert "DNS=8.8.8.8" in result
        assert "Domains=example.com" in result
        assert "DNSOverTLS=yes" in result

    def test_render_systemd_resolved_no_tls(self):
        """DNSOverTLS omitted when False."""
        result = render_systemd_resolved(["8.8.8.8"], [], False)
        assert "DNSOverTLS" not in result

    def test_render_networkmanager_conf(self):
        """Correct NetworkManager conf format."""
        result = render_networkmanager_conf(["8.8.8.8", "1.1.1.1"], [])
        assert "[global-dns-domain-*]" in result
        assert "servers=8.8.8.8,1.1.1.1" in result


# ---------------------------------------------------------------------------
# Collector parser tests
# ---------------------------------------------------------------------------


class TestResolverCollectorParsers:
    def test_parse_resolv_conf(self):
        """Parse resolv.conf correctly."""
        text = (
            "nameserver 8.8.8.8\n"
            "nameserver 1.1.1.1\n"
            "search example.com\n"
            "options ndots:5 timeout:2\n"
        )
        result = parse_resolv_conf(text)
        assert result["nameservers"] == ["8.8.8.8", "1.1.1.1"]
        assert result["search_domains"] == ["example.com"]
        assert result["options"]["ndots"] == 5
        assert result["options"]["timeout"] == 2

    def test_parse_resolv_conf_with_comments(self):
        """Ignore comments in resolv.conf."""
        text = "# comment\nnameserver 8.8.8.8\n; another comment\n"
        result = parse_resolv_conf(text)
        assert result["nameservers"] == ["8.8.8.8"]

    def test_parse_resolv_conf_empty(self):
        """Empty file returns empty lists."""
        result = parse_resolv_conf("")
        assert result["nameservers"] == []
        assert result["search_domains"] == []
        assert result["options"] == {}

    def test_parse_resolv_conf_domain_directive(self):
        """'domain' directive parsed as single search domain."""
        text = "nameserver 8.8.8.8\ndomain example.com\n"
        result = parse_resolv_conf(text)
        assert result["search_domains"] == ["example.com"]

    def test_parse_resolvectl_output(self):
        """Parse resolvectl status output."""
        text = (
            "Global\n"
            "  Current DNS Server: 8.8.8.8\n"
            "  DNS Servers: 8.8.8.8 1.1.1.1\n"
            "  DNS Domain: example.com\n"
        )
        result = parse_resolvectl_output(text)
        assert "8.8.8.8" in result["nameservers"]
        assert "1.1.1.1" in result["nameservers"]
        assert "example.com" in result["search_domains"]

    def test_parse_resolved_conf(self):
        """Parse /etc/systemd/resolved.conf."""
        text = "[Resolve]\nDNS=8.8.8.8 1.1.1.1\nDomains=example.com\nDNSOverTLS=yes\n"
        result = parse_resolved_conf(text)
        assert result["nameservers"] == ["8.8.8.8", "1.1.1.1"]
        assert result["search_domains"] == ["example.com"]
        assert result["options"]["dns_over_tls"] == "yes"

    def test_parse_networkmanager_conf(self):
        """Parse NetworkManager conf."""
        text = (
            "# Managed by Barricade\n"
            "[global-dns-domain-*]\n"
            "servers=8.8.8.8,1.1.1.1\n"
        )
        result = parse_networkmanager_conf(text)
        assert result["nameservers"] == ["8.8.8.8", "1.1.1.1"]
        assert result["search_domains"] == []


# ---------------------------------------------------------------------------
# Diff engine tests
# ---------------------------------------------------------------------------


class TestResolverDiff:
    def test_diff_detects_nameserver_change(self):
        """Detect nameserver order change as drift."""
        current = {"nameservers": ["1.1.1.1", "8.8.8.8"], "search_domains": [], "options": {}}
        desired = {"nameservers": ["8.8.8.8", "1.1.1.1"], "search_domains": [], "options": {}}
        diff = compute_resolver_diff(current, desired)
        assert diff.nameservers_changed is True
        assert diff.has_changes is True

    def test_diff_no_changes(self):
        """No changes when configs match."""
        config = {"nameservers": ["8.8.8.8"], "search_domains": [], "options": {}}
        diff = compute_resolver_diff(config, config)
        assert diff.has_changes is False
        assert diff.nameservers_changed is False
        assert diff.search_domains_changed is False
        assert diff.options_changed is False

    def test_diff_current_none(self):
        """All fields changed when current is None (new host)."""
        desired = {"nameservers": ["8.8.8.8"], "search_domains": [], "options": {}}
        diff = compute_resolver_diff(None, desired)
        assert diff.has_changes is True
        assert diff.nameservers_changed is True
        assert diff.search_domains_changed is True
        assert diff.options_changed is True

    def test_diff_both_none(self):
        """No changes when both are None."""
        diff = compute_resolver_diff(None, None)
        assert diff.has_changes is False

    def test_diff_options_changed(self):
        """Detect options change independently."""
        current = {"nameservers": ["8.8.8.8"], "search_domains": [], "options": {"ndots": 1}}
        desired = {"nameservers": ["8.8.8.8"], "search_domains": [], "options": {"ndots": 5}}
        diff = compute_resolver_diff(current, desired)
        assert diff.nameservers_changed is False
        assert diff.options_changed is True
        assert diff.has_changes is True


# ---------------------------------------------------------------------------
# Playbook generation tests
# ---------------------------------------------------------------------------


def _tasks_for(resolver_type: str) -> list[dict]:
    out = generate_resolver_playbook(
        host_ip="10.0.0.1",
        resolver_type=resolver_type,
        rendered_content="nameserver 8.8.8.8\n",
        ssh_key_path="/tmp/key",
    )
    return out["playbook"][0]["tasks"]


class TestResolverPlaybook:
    @pytest.mark.parametrize("resolver_type", ["resolv_conf", "systemd_resolved", "networkmanager"])
    def test_cloud_init_disable_tasks_lead_playbook(self, resolver_type):
        tasks = _tasks_for(resolver_type)
        assert tasks[0]["ansible.builtin.stat"]["path"] == "/etc/cloud/cloud.cfg.d"
        assert tasks[0]["register"] == "barricade_cloud_init_dir"
        assert tasks[1]["ansible.builtin.copy"]["dest"] == CLOUD_INIT_DISABLE_PATH
        assert tasks[1]["when"] == "barricade_cloud_init_dir.stat.exists"

    def test_resolv_conf_replaces_symlink_before_writing(self):
        tasks = _tasks_for("resolv_conf")
        # tasks[0..1] are the cloud-init guard; backend tasks start at index 2.
        stat_task, rm_task, write_task = tasks[2], tasks[3], tasks[4]
        assert stat_task["ansible.builtin.stat"]["path"] == "/etc/resolv.conf"
        assert rm_task["ansible.builtin.file"] == {"path": "/etc/resolv.conf", "state": "absent"}
        assert "islnk" in rm_task["when"]
        assert write_task["ansible.builtin.copy"]["dest"] == "/etc/resolv.conf"
