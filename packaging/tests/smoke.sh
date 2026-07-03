#!/usr/bin/env bash
# In-container smoke assertions for an installed LabDog package.
#
# Run this *inside* a target-OS container after installing one of the
# artifacts (.deb / .rpm / tarball-via-install.sh). It asserts the install
# landed the right files, created the service account, produced a working
# venv, and reports the expected version. It deliberately does NOT start the
# service (that needs PostgreSQL + Redis + secrets) — this is an install-path
# smoke, the same procedure that was run by hand for earlier releases.
#
# Usage: smoke.sh <expected-version>
set -euo pipefail

EXPECTED="${1:?expected version required (e.g. smoke.sh 0.6.1)}"

PREFIX=/usr/lib/labdog
VENV_PY="$PREFIX/venv/bin/python"
BACKEND="$PREFIX/backend"
UNIT=/usr/lib/systemd/system/labdog.service

fail() { echo "SMOKE FAIL: $*" >&2; exit 1; }
ok()   { echo "  ok  - $*"; }

echo "== LabDog install smoke (expected version: $EXPECTED) =="

# 1. Application code -------------------------------------------------------
[ -x "$VENV_PY" ]            || fail "venv python missing/not executable: $VENV_PY"
[ -d "$BACKEND/app" ]        || fail "backend source missing: $BACKEND/app"
[ -f "$BACKEND/alembic.ini" ] || fail "alembic.ini missing: $BACKEND/alembic.ini"
ok "application code + venv present"

# 2. Bundled action pack (build-time clone of labdog-playbooks) -------------
[ -d "$BACKEND/app/ansible/actions" ] || fail "bundled pack actions dir missing"
for a in alloy-install k8s-upgrade linux-os-upgrade linux-upgrade; do
    [ -d "$BACKEND/app/ansible/actions/$a" ] || fail "bundled action '$a' missing"
done
ok "bundled action pack present (alloy-install, k8s-upgrade, linux-os-upgrade, linux-upgrade)"

# 3. System-integration files ----------------------------------------------
[ -f "$UNIT" ] || fail "systemd unit missing: $UNIT"
grep -q 'ExecStart=/usr/lib/labdog/venv/bin/python -m app' "$UNIT" \
    || fail "systemd unit ExecStart is not the expected 'python -m app' entrypoint"
[ -f /usr/lib/tmpfiles.d/labdog.conf ] || fail "tmpfiles.d config missing"
[ -f /etc/labdog/labdog.toml ]         || fail "config file missing: /etc/labdog/labdog.toml"
ok "systemd unit + tmpfiles.d + config present"

# 4. Owned directories + service account -----------------------------------
[ -d /var/lib/labdog ] || fail "/var/lib/labdog missing"
[ -d /var/log/labdog ] || fail "/var/log/labdog missing"
id labdog >/dev/null 2>&1            || fail "labdog system user not created"
getent group labdog >/dev/null 2>&1 || fail "labdog group not created"
ok "data dirs + labdog service account present"

# 5. The venv imports the app package + key runtime deps -------------------
#    cwd = backend so `app` resolves the installed source tree (with the
#    bundled pack), matching how the systemd unit runs it.
( cd "$BACKEND" && "$VENV_PY" - <<'PY'
import importlib
mods = [
    "app", "app.api.version",
    "fastapi", "starlette", "uvicorn",
    "sqlalchemy", "asyncpg", "alembic",
    "celery", "ansible_runner", "asyncssh", "cryptography",
]
for m in mods:
    importlib.import_module(m)
print("  (imported: %d modules)" % len(mods))
PY
) || fail "venv could not import the app package + runtime dependencies"
ok "venv imports app + runtime deps"

# 6. Reported version matches the release ----------------------------------
#    Guards the dist-metadata regression where packaged installs reported
#    0.0.0 because the wheel was built without the VERSION file.
GOT="$("$VENV_PY" -c 'import importlib.metadata as m; print(m.version("labdog-backend"))')" \
    || fail "could not read labdog-backend distribution metadata"
[ "$GOT" = "$EXPECTED" ] || fail "version mismatch: metadata reports '$GOT', expected '$EXPECTED'"
ok "labdog-backend distribution reports $GOT"

echo "== SMOKE PASS =="
