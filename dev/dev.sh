#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${SCRIPT_DIR}/.."
PIDFILE_DIR="${ROOT_DIR}/.dev-pids"
ENV_FILE="${SCRIPT_DIR}/.env"

# Seed .env with dev-only secrets on first run. LabDog rejects the insecure
# placeholder keys in labdog.toml at startup, so we generate a real pair here
# and persist them for subsequent runs.
ensure_dev_env() {
  if [[ -f "$ENV_FILE" ]] \
     && grep -q "^LABDOG_SECURITY__SECRET_KEY=" "$ENV_FILE" \
     && grep -q "^LABDOG_SECURITY__ENCRYPTION_KEY=" "$ENV_FILE"; then
    return
  fi

  echo "[dev] Seeding ${ENV_FILE} with generated dev secrets..."
  local venv_python="${ROOT_DIR}/backend/.venv/bin/python"
  if [[ ! -x "$venv_python" ]]; then
    echo "[dev] ERROR: backend venv not found at ${venv_python}. Run: cd backend && python -m venv .venv && uv pip install -e '.[dev]'"
    exit 1
  fi

  # Use pure-stdlib generators that don't import app modules — importing
  # app.config here would trigger the very validation we're trying to satisfy.
  local secret enc
  secret=$("$venv_python" -c 'import secrets; print(secrets.token_urlsafe(64))')
  enc=$("$venv_python" -c 'import os, base64; print(base64.b64encode(os.urandom(32)).decode())')

  touch "$ENV_FILE"
  grep -q "^LABDOG_SECURITY__SECRET_KEY=" "$ENV_FILE" \
    || echo "LABDOG_SECURITY__SECRET_KEY=${secret}" >> "$ENV_FILE"
  grep -q "^LABDOG_SECURITY__ENCRYPTION_KEY=" "$ENV_FILE" \
    || echo "LABDOG_SECURITY__ENCRYPTION_KEY=${enc}" >> "$ENV_FILE"
  chmod 600 "$ENV_FILE"
}

# Load .env if present (we may seed it below for commands that need secrets).
load_dev_env() {
  if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  fi
}
load_dev_env

usage() {
  cat <<EOF
Usage: dev/$(basename "$0") <command>

Commands:
  start       Start infrastructure (postgres, redis) + backend + frontend
  stop        Stop all dev processes
  status      Show running dev processes
  logs        Tail all dev logs
  infra       Start only postgres + redis (docker)
  backend     Start only backend (labdog + celery)
  frontend    Start only frontend (next dev)
  migrate     Run alembic upgrade head
  migrate-down  Roll back one migration (alembic downgrade -1)
  migrate-new <msg>  Generate a new migration (alembic revision --autogenerate)
  bundle      Re-fetch the bundled action pack from labdog-playbooks
              (clones at the pinned ref in ./LABDOG_PLAYBOOKS_REF;
              override repo URL via env: LABDOG_PLAYBOOKS_REPO=...
              point at a local working copy via LABDOG_PLAYBOOKS_LOCAL)

Infrastructure (postgres, redis) runs via docker compose.
Backend (labdog, celery worker, celery beat) and frontend (next dev) run as local processes.
EOF
  exit 1
}

ensure_piddir() {
  mkdir -p "${PIDFILE_DIR}"
}

log() {
  echo "[dev] $*"
}

#--- Bundled action pack ---
#
# The bundled pack at backend/app/ansible/ is fetched at container
# build time in production (see Dockerfile Stage 2b) and is gitignored
# in this repo. Dev needs the same content to mimic production -- we
# clone it on demand into backend/app/ansible/ at the pinned ref in
# the repo-root LABDOG_PLAYBOOKS_REF file.
#
# Escape hatch: set LABDOG_PLAYBOOKS_LOCAL=/path/to/working/copy to
# rsync from a sibling labdog-playbooks checkout instead. Useful when
# you're iterating on the playbooks repo and don't want to commit
# every change just to test it here.

BUNDLED_DIR="${ROOT_DIR}/backend/app/ansible"
REF_FILE="${ROOT_DIR}/LABDOG_PLAYBOOKS_REF"

ensure_bundled_pack() {
  # No-op when the directory is already populated (has an `actions`
  # subdir). Operators can force re-fetch via `./dev/dev.sh bundle`.
  if [[ -d "${BUNDLED_DIR}/actions" ]]; then
    return
  fi
  fetch_bundled_pack
}

fetch_bundled_pack() {
  local repo="${LABDOG_PLAYBOOKS_REPO:-https://github.com/open-labdog/labdog-playbooks.git}"
  local ref="${LABDOG_PLAYBOOKS_REF:-}"
  if [[ -z "$ref" && -f "$REF_FILE" ]]; then
    ref="$(tr -d '[:space:]' < "$REF_FILE")"
  fi

  rm -rf "${BUNDLED_DIR}"
  mkdir -p "${BUNDLED_DIR}"

  if [[ -n "${LABDOG_PLAYBOOKS_LOCAL:-}" ]]; then
    if [[ ! -d "${LABDOG_PLAYBOOKS_LOCAL}" ]]; then
      log "ERROR: LABDOG_PLAYBOOKS_LOCAL=${LABDOG_PLAYBOOKS_LOCAL} does not exist"
      exit 1
    fi
    log "Bundling from local checkout: ${LABDOG_PLAYBOOKS_LOCAL}"
    rsync -a --delete \
      --exclude='.git' --exclude='.gitignore' \
      "${LABDOG_PLAYBOOKS_LOCAL}/" "${BUNDLED_DIR}/"
    return
  fi

  if [[ -z "$ref" ]]; then
    log "ERROR: LABDOG_PLAYBOOKS_REF is empty and ${REF_FILE} not found"
    exit 1
  fi

  log "Cloning ${repo}@${ref} into ${BUNDLED_DIR}..."
  local tmp
  tmp="$(mktemp -d)"
  trap "rm -rf '$tmp'" RETURN
  # `--branch` accepts tags and branch names but not raw commit SHAs;
  # fall back to a full clone + checkout for SHA refs (same handling
  # as the Dockerfile and packaging Makefile).
  if ! git clone --depth 1 --branch "$ref" "$repo" "$tmp/upstream" 2>/dev/null; then
    git clone "$repo" "$tmp/upstream"
    git -C "$tmp/upstream" checkout "$ref"
  fi
  rm -rf "$tmp/upstream/.git" "$tmp/upstream/.gitignore"
  rsync -a "$tmp/upstream/" "${BUNDLED_DIR}/"
  log "Bundled pack populated at ${BUNDLED_DIR}"
}

#--- Infrastructure ---

start_infra() {
  log "Starting postgres + redis..."
  docker compose -f "${SCRIPT_DIR}/docker-compose.yml" --env-file "${ENV_FILE}" up -d postgres redis
  log "Waiting for postgres..."
  until docker compose -f "${SCRIPT_DIR}/docker-compose.yml" --env-file "${ENV_FILE}" exec -T postgres pg_isready -U labdog &>/dev/null; do
    sleep 1
  done
  log "Postgres ready."
}

stop_infra() {
  log "Stopping postgres + redis..."
  docker compose -f "${SCRIPT_DIR}/docker-compose.yml" --env-file "${ENV_FILE}" stop postgres redis 2>/dev/null || true
}

#--- Backend ---

VENV="${ROOT_DIR}/backend/.venv/bin"

start_backend() {
  ensure_piddir
  local logdir="${ROOT_DIR}/.dev-logs"
  mkdir -p "${logdir}"

  if [[ ! -x "${VENV}/python" ]]; then
    log "ERROR: Backend venv not found at ${VENV}. Run: cd backend && python -m venv .venv && uv pip install ."
    exit 1
  fi

  ensure_dev_env
  load_dev_env
  ensure_bundled_pack

  # Run migrations
  log "Running migrations..."
  (cd "${ROOT_DIR}/backend" && LABDOG_CONFIG="${SCRIPT_DIR}/labdog.toml" "${VENV}/alembic" upgrade head)

  # Prepend the venv bin to PATH so child processes (e.g. ansible-runner
  # spawning ansible-playbook) can resolve console scripts installed in
  # the venv. Without this, pip-installed CLIs like ansible-playbook are
  # missing from subprocess PATH even though the Python imports work.
  local venv_path="${VENV}:${PATH}"

  # LabDog (python -m app with auto-reload, no embedded celery, skip migrate since we ran it above)
  # LABDOG_DEV_MODE disables static frontend serving so the Next.js dev server on :3000 is used.
  log "Starting labdog..."
  (cd "${ROOT_DIR}/backend" && PATH="${venv_path}" LABDOG_DEV_MODE=1 LABDOG_CONFIG="${SCRIPT_DIR}/labdog.toml" "${VENV}/python" -m app --reload --no-celery --skip-migrate \
    >"${logdir}/labdog.log" 2>&1) &
  echo $! > "${PIDFILE_DIR}/labdog.pid"

  # Celery worker
  log "Starting celery worker..."
  (cd "${ROOT_DIR}/backend" && PATH="${venv_path}" LABDOG_CONFIG="${SCRIPT_DIR}/labdog.toml" "${VENV}/celery" -A app.tasks worker \
    --max-tasks-per-child=100 -Q default,long_running --loglevel=info \
    >"${logdir}/celery-worker.log" 2>&1) &
  echo $! > "${PIDFILE_DIR}/celery-worker.pid"

  # Celery beat
  log "Starting celery beat..."
  (cd "${ROOT_DIR}/backend" && PATH="${venv_path}" LABDOG_CONFIG="${SCRIPT_DIR}/labdog.toml" "${VENV}/celery" -A app.tasks beat \
    --scheduler redbeat.RedBeatScheduler --loglevel=info \
    >"${logdir}/celery-beat.log" 2>&1) &
  echo $! > "${PIDFILE_DIR}/celery-beat.pid"

  log "Backend running — labdog at http://localhost:8000"
}

kill_pattern() {
  local pattern="$1"
  local label="$2"
  local pids
  pids=$(pgrep -f "$pattern" 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    log "Stopping ${label}..."
    echo "$pids" | xargs kill 2>/dev/null || true
    for _ in {1..10}; do
      pgrep -f "$pattern" &>/dev/null || break
      sleep 0.5
    done
    pgrep -f "$pattern" &>/dev/null && echo "$pids" | xargs kill -9 2>/dev/null || true
  fi
}

stop_backend() {
  kill_pattern "python -m app" "labdog"
  kill_pattern "celery -A app.tasks worker" "celery-worker"
  kill_pattern "celery -A app.tasks beat" "celery-beat"
  rm -f "${PIDFILE_DIR}"/{labdog,celery-worker,celery-beat}.pid 2>/dev/null
}

#--- Frontend ---

start_frontend() {
  ensure_piddir
  local logdir="${ROOT_DIR}/.dev-logs"
  mkdir -p "${logdir}"

  log "Starting next dev..."
  (cd "${ROOT_DIR}/frontend" && NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev \
    >"${logdir}/frontend.log" 2>&1) &
  echo $! > "${PIDFILE_DIR}/frontend.pid"

  log "Frontend running — http://localhost:3000"
}

stop_frontend() {
  kill_pattern "next-server" "frontend"
  kill_pattern "next dev" "next dev"
  rm -f "${PIDFILE_DIR}/frontend.pid" 2>/dev/null
}

#--- Migrations ---

run_migrate() {
  if [[ ! -x "${VENV}/alembic" ]]; then
    log "ERROR: alembic not found in venv. Run: cd backend && uv pip install ."
    exit 1
  fi
  ensure_dev_env
  load_dev_env
  log "Running migrations..."
  (cd "${ROOT_DIR}/backend" && LABDOG_CONFIG="${SCRIPT_DIR}/labdog.toml" "${VENV}/alembic" upgrade head)
  log "Migrations complete."
}

run_migrate_down() {
  if [[ ! -x "${VENV}/alembic" ]]; then
    log "ERROR: alembic not found in venv."
    exit 1
  fi
  log "Rolling back one migration..."
  (cd "${ROOT_DIR}/backend" && LABDOG_CONFIG="${SCRIPT_DIR}/labdog.toml" "${VENV}/alembic" downgrade -1)
  log "Rollback complete."
}

run_migrate_new() {
  local msg="${1:-}"
  if [[ -z "$msg" ]]; then
    log "ERROR: Provide a migration message: ./dev/dev.sh migrate-new 'add users table'"
    exit 1
  fi
  if [[ ! -x "${VENV}/alembic" ]]; then
    log "ERROR: alembic not found in venv."
    exit 1
  fi
  log "Generating migration: ${msg}"
  (cd "${ROOT_DIR}/backend" && LABDOG_CONFIG="${SCRIPT_DIR}/labdog.toml" "${VENV}/alembic" revision --autogenerate -m "$msg")
  log "Migration generated."
}

#--- Status ---

port_listening() {
  ss -tlnp 2>/dev/null | grep -q ":${1} " 2>/dev/null
}

show_status() {
  local running=0
  echo ""
  echo "=== Dev Process Status ==="
  echo ""

  for svc in postgres redis; do
    if docker compose -f "${SCRIPT_DIR}/docker-compose.yml" --env-file "${ENV_FILE}" ps --status running "${svc}" 2>/dev/null | grep -q "${svc}"; then
      printf "  %-20s %s\n" "${svc}" "running (docker)"
      running=$((running + 1))
    else
      printf "  %-20s %s\n" "${svc}" "stopped"
    fi
  done

  if port_listening 8000; then
    printf "  %-20s %s\n" "labdog" "running (:8000)"
    running=$((running + 1))
  else
    printf "  %-20s %s\n" "labdog" "stopped"
  fi

  if pgrep -f "celery -A app.tasks worker" &>/dev/null; then
    printf "  %-20s %s\n" "celery-worker" "running"
    running=$((running + 1))
  else
    printf "  %-20s %s\n" "celery-worker" "stopped"
  fi

  if pgrep -f "celery -A app.tasks beat" &>/dev/null; then
    printf "  %-20s %s\n" "celery-beat" "running"
    running=$((running + 1))
  else
    printf "  %-20s %s\n" "celery-beat" "stopped"
  fi

  if port_listening 3000; then
    printf "  %-20s %s\n" "frontend" "running (:3000)"
    running=$((running + 1))
  else
    printf "  %-20s %s\n" "frontend" "stopped"
  fi

  echo ""
  if [[ $running -eq 0 ]]; then
    echo "Nothing running."
  else
    echo "${running} process(es) running."
  fi
}

#--- Logs ---

tail_logs() {
  local logdir="${ROOT_DIR}/.dev-logs"
  if [[ ! -d "$logdir" ]] || [[ -z "$(ls -A "$logdir" 2>/dev/null)" ]]; then
    echo "No logs found. Start the dev server first."
    exit 1
  fi
  tail -f "${logdir}"/*.log
}

#--- Main ---

case "${1:-}" in
  start)
    start_infra
    start_backend
    start_frontend
    echo ""
    log "All services started."
    log "  Backend:  http://localhost:8000"
    log "  Frontend: http://localhost:3000"
    log ""
    log "Run './dev/dev.sh logs' to tail logs, './dev/dev.sh stop' to shut down."
    ;;
  stop)
    stop_frontend
    stop_backend
    stop_infra
    log "All services stopped."
    ;;
  status)
    show_status
    ;;
  logs)
    tail_logs
    ;;
  infra)
    start_infra
    ;;
  backend)
    start_backend
    ;;
  frontend)
    start_frontend
    ;;
  migrate)
    run_migrate
    ;;
  migrate-down)
    run_migrate_down
    ;;
  migrate-new)
    run_migrate_new "${2:-}"
    ;;
  bundle)
    fetch_bundled_pack
    ;;
  *)
    usage
    ;;
esac
