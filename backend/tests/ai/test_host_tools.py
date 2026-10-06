"""The inventory tools tell the model which user its commands run as.

The prompt says to use sudo only when that user is not root. Without the
user on each host, that rule is a guess: the default is root, where sudo
may not be installed, and a fleet can mix root and non-root logins.
"""

from __future__ import annotations

from app.ai.tools.base import ToolContext
from app.ai.tools.hosts import GET_HOST_FACTS, LIST_HOSTS
from tests.conftest import create_host


def _ctx(db, host_ids: list[int]) -> ToolContext:
    return ToolContext(db=db, session_id=1, autonomy_level="read_only", target_host_ids=host_ids)


async def test_list_hosts_shows_each_hosts_user(db) -> None:
    root = await create_host(db)
    admin = await create_host(db)
    admin.ssh_user = "admin"
    await db.flush()
    result = await LIST_HOSTS.run(_ctx(db, [root.id, admin.id]), {})
    assert f"id={root.id} {root.hostname} (10.0.0.1) user=root " in result.content
    assert f"id={admin.id} {admin.hostname} (10.0.0.1) user=admin " in result.content


async def test_host_facts_show_the_user(db) -> None:
    host = await create_host(db)
    host.ssh_user = "admin"
    await db.flush()
    result = await GET_HOST_FACTS.run(_ctx(db, [host.id]), {"host_id": host.id})
    assert "ssh_user: admin" in result.content
