"""Load extensions (the Enterprise packages) installed next to unbork.

An extension is a Python distribution that declares an entry point in the
``unbork.plugins`` group::

    [project.entry-points."unbork.plugins"]
    sso = "unbork_enterprise.sso:register"

The entry point names a callable that takes the FastAPI app. It adds its
routes and calls the documented hooks (``set_permission_resolver``,
``register_login_provider_source`` ...) from inside it. It also carries a
``requires_plugin_api = "1.0"`` attribute naming the contract version it
was written against (``app.auth.permissions.PLUGIN_API``):

* same major, minor at most ours: loaded;
* anything else, or no attribute: skipped, with an error in the log.

A plugin that raises while loading or registering is skipped as well,
never fatal. The point of the edition split is that a Community install
with a broken or stale Enterprise package still starts and still lets an
admin sign in to see what is wrong. Release lines are patched side by side,
so an Enterprise package built for 1.1 meeting a 1.2 core is a real case,
and the version check is what turns it into a log line instead of an
``AttributeError`` halfway through startup.

Plugins load once per process, in ``create_app()``, after the core routers
and before the SPA fallback (whose catch-all route would otherwise shadow
the plugin's paths).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points

from fastapi import FastAPI

from app.auth.permissions import PLUGIN_API

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "unbork.plugins"
MIGRATIONS_GROUP = "unbork.migrations"


@dataclass(frozen=True)
class LoadedPlugin:
    name: str
    #: ``name==version`` of the distribution that provided it, if known.
    distribution: str | None
    requires_plugin_api: str
    #: Top-level package of the entry point (``unbork_enterprise``), so
    #: Community's own checks can tell the routes it added from core's.
    package: str = ""


#: What this process loaded, in load order. Read-only for everyone else.
loaded_plugins: list[LoadedPlugin] = []


def _parse(version: str) -> tuple[int, int] | None:
    try:
        major, minor = version.split(".")
        return int(major), int(minor)
    except (AttributeError, ValueError):
        return None


def is_compatible(required: str, provided: str = PLUGIN_API) -> bool:
    """Whether a plugin written against *required* runs on *provided*."""
    want, have = _parse(required), _parse(provided)
    if want is None or have is None:
        return False
    return want[0] == have[0] and want[1] <= have[1]


def _distribution(ep: EntryPoint) -> str | None:
    dist = getattr(ep, "dist", None)
    if dist is None:
        return None
    return f"{dist.name}=={dist.version}"


def load_plugins(app: FastAPI, eps: Iterable[EntryPoint] | None = None) -> list[LoadedPlugin]:
    """Load and register every installed plugin; return the ones that loaded.

    *eps* exists for tests; production reads the installed entry points.
    """
    if eps is None:
        eps = entry_points(group=ENTRY_POINT_GROUP)
    loaded: list[LoadedPlugin] = []
    for ep in sorted(eps, key=lambda e: e.name):
        dist = _distribution(ep)
        try:
            register: Callable[[FastAPI], object] = ep.load()
        except Exception:
            logger.exception("plugin %s (%s): failed to import; skipped", ep.name, dist or ep.value)
            continue
        required = getattr(register, "requires_plugin_api", None)
        if not isinstance(required, str) or not is_compatible(required):
            logger.error(
                "plugin %s (%s) needs plugin API %r but this unbork provides %s; skipped. "
                "Install the plugin release built for this unbork version.",
                ep.name,
                dist or ep.value,
                required,
                PLUGIN_API,
            )
            continue
        try:
            register(app)
        except Exception:
            logger.exception(
                "plugin %s (%s): register() failed; skipped", ep.name, dist or ep.value
            )
            continue
        plugin = LoadedPlugin(
            name=ep.name,
            distribution=dist,
            requires_plugin_api=required,
            package=ep.value.split(":", 1)[0].split(".", 1)[0],
        )
        loaded.append(plugin)
        logger.info("plugin %s (%s) loaded", ep.name, dist or ep.value)
    loaded_plugins[:] = loaded
    return loaded


def run_plugin_migrations(eps: Iterable[EntryPoint] | None = None) -> list[str]:
    """Bring each installed plugin's own tables up to date; return their names.

    An extension with tables declares a second entry point, in the
    ``unbork.migrations`` group, naming a callable ``upgrade()`` (with the
    same ``requires_plugin_api`` attribute) that runs its own Alembic
    environment against ``settings.database.url``. It must keep its own
    version table (``alembic_version_enterprise``, say), never core's
    ``alembic_version``: a revision core does not know in core's table
    would stop a Community install from starting once the extension is
    uninstalled.

    Called by ``python -m app`` right after core's own ``upgrade head``,
    only in the roles that migrate. A failing plugin migration is logged
    and skipped like a failing plugin: core keeps running, and the plugin
    is expected to notice its schema is behind and refuse to register.
    """
    if eps is None:
        eps = entry_points(group=MIGRATIONS_GROUP)
    done: list[str] = []
    for ep in sorted(eps, key=lambda e: e.name):
        dist = _distribution(ep) or ep.value
        try:
            upgrade = ep.load()
        except Exception:
            logger.exception("plugin migrations %s (%s): failed to import; skipped", ep.name, dist)
            continue
        required = getattr(upgrade, "requires_plugin_api", None)
        if not isinstance(required, str) or not is_compatible(required):
            logger.error(
                "plugin migrations %s (%s) need plugin API %r, this unbork provides %s; skipped",
                ep.name,
                dist,
                required,
                PLUGIN_API,
            )
            continue
        try:
            logger.info("Running plugin migrations %s (%s) ...", ep.name, dist)
            upgrade()
        except Exception:
            logger.exception("plugin migrations %s (%s) failed; skipped", ep.name, dist)
            continue
        done.append(ep.name)
    return done
