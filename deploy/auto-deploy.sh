#!/usr/bin/env bash
#
# auto-deploy.sh - pull based deployment for /opt/expert
#
# WHY PULL BASED
#
# The deploy target sits behind the CCHMC research VPN, so nothing on the public
# internet can reach it. GitHub's hosted runners therefore cannot SSH in, and a
# self-hosted runner needs an org permission that is not available here. This
# script inverts the problem: the server polls GitHub over outbound HTTPS, which
# is already allowed, and redeploys itself when main moves.
#
# It needs no GitHub credentials for a public repo, no inbound firewall rule and
# no secrets in CI.
#
# SAFETY
#
# These commands are deliberately absent, and must stay absent:
#   docker compose down -v     destroys the postgres volume (chat history, feedback)
#   docker volume rm ...       same
#   docker system prune        can reap volumes as well
#   git clean -xfd             deletes .env and the 163 MB dump/
#   make clean / make reset-db down -v, and a forced 163 MB graph reload
#
# `git reset --hard` moves tracked files only, so .env and dump/ are untouched.
#
# USAGE
#   ./auto-deploy.sh            poll once, deploy only if main moved
#   ./auto-deploy.sh --force    redeploy the current commit regardless
#   ./auto-deploy.sh --check    report status and exit, change nothing

set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/expert}"
BRANCH="${DEPLOY_BRANCH:-main}"
HEALTH_TRIES="${HEALTH_TRIES:-60}"
HEALTH_SLEEP="${HEALTH_SLEEP:-5}"
LOCK="/tmp/dbeexpert-deploy.lock"

log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
die() { log "ERROR: $*"; exit 1; }

MODE="poll"
case "${1:-}" in
  --force) MODE="force" ;;
  --check) MODE="check" ;;
  "") ;;
  *) die "unknown argument: $1" ;;
esac

# Only one deploy at a time. Two concurrent docker builds in the same directory
# race on the build cache and on the git checkout.
exec 9>"$LOCK"
if ! flock -n 9; then
  log "another deploy is already running, exiting"
  exit 0
fi

cd "$DEPLOY_DIR" || die "$DEPLOY_DIR does not exist"

# ---- preflight: fail before touching anything ----
[ -d .git ]            || die "$DEPLOY_DIR is not a git repository"
[ -f .env ]            || die ".env is missing; the stack cannot start without it"
[ -f dump/neo4j.dump ] || die "dump/neo4j.dump is missing"
[ -w .git ]            || die "cannot write to .git as $(whoami); fix ownership of $DEPLOY_DIR"
docker info >/dev/null 2>&1 || die "docker is not usable by $(whoami)"

git fetch --prune --quiet origin

LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse "origin/$BRANCH")"

if [ "$MODE" = "check" ]; then
  log "local  $(git rev-parse --short HEAD)  $(git log -1 --pretty=%s)"
  log "remote $(git rev-parse --short "origin/$BRANCH")  $(git log -1 --pretty=%s "origin/$BRANCH")"
  [ "$LOCAL" = "$REMOTE" ] && log "up to date" || log "BEHIND: a deploy would run"
  exit 0
fi

if [ "$LOCAL" = "$REMOTE" ] && [ "$MODE" != "force" ]; then
  log "already at $(git rev-parse --short HEAD), nothing to do"
  exit 0
fi

log "deploying $(git rev-parse --short "$LOCAL") -> $(git rev-parse --short "$REMOTE")"

# ---- move the working tree ----
# Untracked and ignored paths, which is where .env and dump/ live, are left
# alone by reset --hard. Local edits to TRACKED files are discarded, which is
# intended for a deploy target.
git reset --hard --quiet "origin/$BRANCH"
log "checked out $(git rev-parse --short HEAD)  $(git log -1 --pretty=%s)"

# ---- rebuild and start ----
# --build is mandatory: the frontend bakes BASE_PATH into every asset URL at
# build time, so without it compose reports success while serving the old image.
log "building and starting"
docker compose up -d --build

# ---- reclaim space ----
# Every deploy builds two images, so the previous ones become dangling and the
# buildkit cache grows. Left alone the disk fills, and a full disk is the most
# likely way to actually lose the postgres volume: Neo4j and Postgres start
# failing writes long before anyone types a destructive command.
#
# `image prune` without -a removes ONLY dangling images, never a tagged one in
# use. `docker system prune` and any `--volumes` form are deliberately absent:
# those can take named volumes with them.
log "reclaiming space from dangling images and old build cache"
docker image prune -f >/dev/null 2>&1 || true
docker builder prune -f --filter 'until=168h' >/dev/null 2>&1 || true
df -h "$DEPLOY_DIR" | tail -1 | awk '{print "  disk: "$4" free ("$5" used)"}'

# ---- health gate ----
PORT="$(grep -E '^BACKEND_PORT=' .env | cut -d= -f2 | tr -d '[:space:]' || true)"
PORT="${PORT:-8011}"
log "polling http://127.0.0.1:${PORT}/api/health"

healthy=0
for i in $(seq 1 "$HEALTH_TRIES"); do
  body="$(curl -fsS --max-time 5 "http://127.0.0.1:${PORT}/api/health" 2>/dev/null || true)"
  if printf '%s' "$body" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
    # "ok" with an empty graph means the dump never restored. The API answers,
    # but every question would return nothing, so treat it as a failure.
    if printf '%s' "$body" | grep -qE '"nodes"[[:space:]]*:[[:space:]]*[1-9]'; then
      healthy=1
      log "healthy after ~$((i * HEALTH_SLEEP))s"
      printf '%s\n' "$body"
      break
    fi
    log "backend is up but the graph reports 0 nodes, still waiting"
  fi
  sleep "$HEALTH_SLEEP"
done

if [ "$healthy" -ne 1 ]; then
  log "did not become healthy, rolling back to $(git rev-parse --short "$LOCAL")"
  docker compose logs --tail 60 backend || true
  git reset --hard --quiet "$LOCAL"
  docker compose up -d --build
  die "deploy failed and was rolled back"
fi

# ---- frontend gate ----
# The backend probe cannot see a broken frontend. BASE_PATH is a Vite build ARG
# baked into every asset URL, so a wrong value serves a page whose script tags
# all 404 while /api/health stays green.
FPORT="$(grep -E '^FRONTEND_PORT=' .env | cut -d= -f2 | tr -d '[:space:]' || true)"
FPORT="${FPORT:-8080}"
BASE="$(grep -E '^BASE_PATH=' .env | cut -d= -f2 | tr -d '[:space:]' || true)"
BASE="${BASE:-/}"

html="$(curl -fsS --max-time 10 "http://127.0.0.1:${FPORT}/" 2>/dev/null || true)"
if [ -z "$html" ]; then
  log "WARNING: frontend on port ${FPORT} served nothing; the backend is healthy but the UI may be down"
elif printf '%s' "$html" | grep -q "${BASE}assets/"; then
  log "frontend OK: serving assets under ${BASE}"
else
  log "WARNING: frontend is up but its asset paths do not match BASE_PATH=${BASE}."
  log "         a stale build is being served; the page will load blank with 404s."
fi

log "deploy complete at $(git rev-parse --short HEAD)"
