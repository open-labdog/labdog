"""SEC-41: ``$`` matches before a trailing newline, so ``^…$`` validators
accepted ``"name\\n"``. An alias ending in a newline then split
``/etc/hosts`` into a second, unconfigured line (the SEC-24 impact)."""

import pytest
from pydantic import ValidationError

from app.cron.schemas import CronJobCreate
from app.hosts_mgmt.merge import render_hosts_file
from app.hosts_mgmt.schemas import EffectiveHostsEntryResponse, HostsEntryCreate
from app.packages.schemas import PackageRuleCreate
from app.schemas.git_repos import _validate_branch
from app.services.schemas import ServiceRuleCreate
from app.user_mgmt.schemas import LinuxUserCreate


@pytest.mark.parametrize(
    "build",
    [
        lambda: HostsEntryCreate(ip_address="10.0.0.1", hostname="a.lan\n"),
        lambda: HostsEntryCreate(
            ip_address="10.0.0.1", hostname="a.lan", aliases=["b\n10.6.6.6 mirror.example"]
        ),
        lambda: HostsEntryCreate(ip_address="10.0.0.1", hostname="a.lan", aliases=["b\n"]),
        lambda: CronJobCreate(name="job\n", schedule="* * * * *", command="true"),
        lambda: CronJobCreate(name="job", user="root\n", schedule="* * * * *", command="true"),
        lambda: PackageRuleCreate(package_name="curl\n"),
        lambda: LinuxUserCreate(username="alice\n"),
        lambda: ServiceRuleCreate(service_name="nginx\n", state="running", enabled=True),
    ],
)
def test_a_trailing_newline_is_refused(build):
    with pytest.raises(ValidationError):
        build()


def test_a_branch_with_a_trailing_newline_is_refused():
    with pytest.raises(ValueError):
        _validate_branch("main\n")


def _entry(**kw) -> EffectiveHostsEntryResponse:
    base = dict(
        ip_address="10.0.0.1",
        hostname="a.lan",
        aliases=[],
        comment=None,
        is_system=False,
        source="group",
        source_id=1,
        source_name="g",
    )
    base.update(kw)
    return EffectiveHostsEntryResponse(**base)


@pytest.mark.parametrize(
    "bad",
    [
        {"aliases": ["b\n10.6.6.6 mirror.example"]},
        {"hostname": "a.lan\r\n10.6.6.6 mirror.example"},
        {"ip_address": "10.0.0.1\n10.6.6.6"},
        {"aliases": ["b#c"]},
    ],
)
def test_render_drops_an_entry_that_would_add_a_line(bad):
    """Rows already in the database, and GitOps imports, reach the renderer
    without the schema, so it refuses the entry itself."""
    out = render_hosts_file([_entry(hostname="ok.lan"), _entry(**bad)])
    assert "mirror.example" not in out
    assert "10.6.6.6" not in out
    assert "ok.lan" in out
    assert len(out.strip().splitlines()) == 2
