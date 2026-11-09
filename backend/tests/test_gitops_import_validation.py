"""SEC-43: GitOps YAML went through plain ``str`` fields, skipping the rules
the API applies, so Git could carry a value the API would refuse."""

import pytest

from app.gitops.importer import import_group_from_yaml
from tests.conftest import create_group

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "body",
    [
        # A unit path outside /etc/systemd/system.
        "services:\n  - service_name: ../../tmp/evil\n    state: running\n",
        "packages:\n  - package_name: 'curl; id'\n",
        "package_repositories:\n  - name: r\n    url: ftp://mirror\n    repo_type: apt\n",
        # An alias with a space becomes two names on the /etc/hosts line.
        "hosts_entries:\n  - ip_address: 10.0.0.1\n    hostname: a.lan\n"
        "    aliases: ['b.lan 10.6.6.6']\n",
        "hosts_entries:\n  - ip_address: not-an-ip\n    hostname: a.lan\n",
        "firewall:\n  rules:\n    - action: allow\n      protocol: tcp\n"
        "      direction: input\n      port: 70000\n",
    ],
)
async def test_a_value_the_api_refuses_fails_the_import(db, body):
    group = await create_group(db, name="sec43")
    group.gitops_enabled = True
    group.gitops_file_path = "test.yaml"
    await db.commit()

    result = await import_group_from_yaml(
        group_id=group.id, yaml_content=f"group: sec43\n{body}", commit_sha="sha", db=db
    )

    assert result.success is False
    assert "Invalid" in (result.error_message or ""), result.error_message
