"""Container health check for every role: ``python -m app.healthcheck``.

The image's ``HEALTHCHECK`` runs this. Which checks apply depends on
``LABDOG_ROLE`` (see :mod:`app.celery_manager`):

* ``all`` / ``api``: ``/health/ready`` on the local API, as before. In
  ``all`` that already covers the Celery children (BUG-72).
* a Celery worker role: the worker answers a broker ping as
  ``<worker>@<this hostname>``.
* ``beat``: the RedBeat lock key is held. Beat refreshes it on every tick,
  so a key that is still there means a scheduler is alive somewhere —
  and with exactly one beat container, that is this one.

Exits 0 when every applicable check passes, 1 otherwise, printing which
one failed.
"""

from __future__ import annotations

import os
import socket
import sys


def _api_ready() -> str | None:
    """``/health/ready`` on this container, the same probe the image used before."""
    import urllib.request  # noqa: PLC0415

    from app.config import settings  # noqa: PLC0415

    url = f"http://localhost:{settings.server.port}/health/ready"
    try:
        # Fixed http://localhost URL, nothing user-supplied.
        with urllib.request.urlopen(url, timeout=4) as r:  # noqa: S310  # nosec B310
            return None if r.status == 200 else f"api: HTTP {r.status}"
    except Exception as exc:  # noqa: BLE001
        return f"api: {exc}"


def _worker_answers(name: str) -> str | None:
    from app.tasks import celery_app  # noqa: PLC0415

    node = f"{name}@{socket.gethostname()}"
    try:
        replies = celery_app.control.ping(destination=[node], timeout=3.0)
    except Exception as exc:  # noqa: BLE001
        return f"{name}: {exc}"
    return None if replies else f"{name}: no ping reply from {node}"


def _beat_lock_held() -> str | None:
    import redis  # noqa: PLC0415

    from app.config import settings  # noqa: PLC0415
    from app.tasks import celery_app  # noqa: PLC0415

    key = celery_app.conf.get("redbeat_lock_key") or "redbeat::lock"
    try:
        held = redis.from_url(settings.redis.url).exists(key)
    except Exception as exc:  # noqa: BLE001
        return f"beat: {exc}"
    return None if held else f"beat: lock {key!r} not held"


def main() -> int:
    from app.celery_manager import API_ROLES, parse_roles, processes_for  # noqa: PLC0415

    roles = parse_roles(os.environ.get("LABDOG_ROLE"))
    failures: list[str] = []
    if any(r in API_ROLES for r in roles):
        failures.append(_api_ready())
    else:
        for name, queues in processes_for(roles).items():
            failures.append(_beat_lock_held() if queues is None else _worker_answers(name))
    failures = [f for f in failures if f]
    for f in failures:
        print(f, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
