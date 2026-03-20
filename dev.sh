#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE_DIR="${SCRIPT_DIR}/.dev-pids"

# Load .env if present
if [[ -f "${SCRIPT_DIR}/.env" ]]; then
  set -a
  source "${SCRIPT_DIR}/.env"
  set +a
fi

usage() {
  cat <<EOF
Usage: $(basename "$0") <command>

Commands:
  start       Start infrastructure (postgres, redis) + backend + frontend
  stop        Stop all dev processes
  status      Show running dev processes
  logs        Tail all dev logs
  infra       Start only postgres + redis (docker)
  backend     Start only backend (barricade + celery)
  frontend    Start only frontend (next dev)
  migrate     Run alembic upgrade head
  migrate-down  Roll back one migration (alembic downgrade -1)
  migrate-new <msg>  Generate a new migration (alembic revision --autogenerate)

Infrastructure (postgres, redis) runs via docker compose.
Backend (barricade, celery worker, celery beat) and frontend (next dev) run as local processes.
EOF
  exit 1
}

ensure_piddir() {
  mkdir -p "${PIDFILE_DIR}"
}

log() {
  echo "[dev] $*"
}

#--- Infrastructure ---

start_infra() {
  log "Starting postgres + redis..."
  docker compose -f "${SCRIPT_DIR}/docker-compose.yml" up -d postgres redis
  log "Waiting for postgres..."
  until docker compose -f "${SCRIPT_DIR}/docker-compose.yml" exec -T postgres pg_isready -U barricade &>/dev/null; do
    sleep 1
  done
  log "Postgres ready."
}

stop_infra() {
  log "Stopping postgres + redis..."
  docker compose -f "${SCRIPT_DIR}/docker-compose.yml" stop postgres redis 2>/dev/null || true
}

#--- Backend ---

VENV="${SCRIPT_DIR}/backend/.venv/bin"

start_backend() {
  ensure_piddir
  local logdir="${SCRIPT_DIR}/.dev-logs"
  mkdir -p "${logdir}"

  if [[ ! -x "${VENV}/python" ]]; then
    log "ERROR: Backend venv not found at ${VENV}. Run: cd backend && python -m venv .venv && uv pip install ."
    exit 1
  fi

  # Run migrations
  log "Running migrations..."
  (cd "${SCRIPT_DIR}/backend" && "${VENV}/alembic" upgrade head)

  # Barricade (python -m app with auto-reload, no embedded celery, skip migrate since we ran it above)
  log "Starting barricade..."
  (cd "${SCRIPT_DIR}/backend" && "${VENV}/python" -m app --reload --no-celery --skip-migrate \
    >"${logdir}/barricade.log" 2>&1) &
  echo $! > "${PIDFILE_DIR}/barricade.pid"

  # Celery worker
  log "Starting celery worker..."
  (cd "${SCRIPT_DIR}/backend" && "${VENV}/celery" -A app.tasks worker \
    --max-tasks-per-child=100 -Q default,long_running --loglevel=info \
    >"${logdir}/celery-worker.log" 2>&1) &
  echo $! > "${PIDFILE_DIR}/celery-worker.pid"

  # Celery beat
  log "Starting celery beat..."
  (cd "${SCRIPT_DIR}/backend" && "${VENV}/celery" -A app.tasks beat \
    --scheduler redbeat.RedBeatScheduler --loglevel=info \
    >"${logdir}/celery-beat.log" 2>&1) &
  echo $! > "${PIDFILE_DIR}/celery-beat.pid"

  log "Backend running — barricade at http://localhost:8000"
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
  kill_pattern "python -m app" "barricade"
  kill_pattern "celery -A app.tasks worker" "celery-worker"
  kill_pattern "celery -A app.tasks beat" "celery-beat"
  rm -f "${PIDFILE_DIR}"/{barricade,celery-worker,celery-beat}.pid 2>/dev/null
}

#--- Frontend ---

start_frontend() {
  ensure_piddir
  local logdir="${SCRIPT_DIR}/.dev-logs"
  mkdir -p "${logdir}"

  log "Starting next dev..."
  (cd "${SCRIPT_DIR}/frontend" && NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev \
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
  log "Running migrations..."
  (cd "${SCRIPT_DIR}/backend" && "${VENV}/alembic" upgrade head)
  log "Migrations complete."
}

run_migrate_down() {
  if [[ ! -x "${VENV}/alembic" ]]; then
    log "ERROR: alembic not found in venv."
    exit 1
  fi
  log "Rolling back one migration..."
  (cd "${SCRIPT_DIR}/backend" && "${VENV}/alembic" downgrade -1)
  log "Rollback complete."
}

run_migrate_new() {
  local msg="${1:-}"
  if [[ -z "$msg" ]]; then
    log "ERROR: Provide a migration message: ./dev.sh migrate-new 'add users table'"
    exit 1
  fi
  if [[ ! -x "${VENV}/alembic" ]]; then
    log "ERROR: alembic not found in venv."
    exit 1
  fi
  log "Generating migration: ${msg}"
  (cd "${SCRIPT_DIR}/backend" && "${VENV}/alembic" revision --autogenerate -m "$msg")
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
    if docker compose -f "${SCRIPT_DIR}/docker-compose.yml" ps --status running "${svc}" 2>/dev/null | grep -q "${svc}"; then
      printf "  %-20s %s\n" "${svc}" "running (docker)"
      running=$((running + 1))
    else
      printf "  %-20s %s\n" "${svc}" "stopped"
    fi
  done

  if port_listening 8000; then
    printf "  %-20s %s\n" "barricade" "running (:8000)"
    running=$((running + 1))
  else
    printf "  %-20s %s\n" "barricade" "stopped"
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
  local logdir="${SCRIPT_DIR}/.dev-logs"
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
    log "Run './dev.sh logs' to tail logs, './dev.sh stop' to shut down."
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
  *)
    usage
    ;;
esac
