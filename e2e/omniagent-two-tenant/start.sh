#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
PREFIX=oa-two-tenant-e2e
NETWORK=$PREFIX-network
PG=$PREFIX-postgres
BACKEND=$PREFIX-backend
FRONTEND=$PREFIX-frontend
VOLUME=$PREFIX-artifacts
FIXTURE_FILE="$ROOT/.codex_tmp/omniagent-two-tenant-fixture.json"
BACKEND_IMAGE='ghcr.io/qbuby/agent-eval-backend@sha256:fe886eb36f9549b9d2bf6bd65b5e8841e1b57ed6000d4c2316f701435ea31527'
FRONTEND_IMAGE='agent-eval-frontend:oa-two-tenant-e2e'

cleanup() {
  docker rm -f "$FRONTEND" "$BACKEND" "$PG" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
  docker volume rm "$VOLUME" >/dev/null 2>&1 || true
  docker image rm "$FRONTEND_IMAGE" >/dev/null 2>&1 || true
  rm -f "$FIXTURE_FILE"
}

cleanup
trap cleanup EXIT INT TERM
mkdir -p "$ROOT/.codex_tmp"

docker network create "$NETWORK" >/dev/null
docker volume create "$VOLUME" >/dev/null
docker run -d --name "$PG" --network "$NETWORK" --network-alias postgres \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=oa-two-tenant-e2e \
  -e POSTGRES_DB=agent_eval \
  postgres:16-alpine >/dev/null

attempt=0
until docker run --rm --network "$NETWORK" postgres:16-alpine \
  pg_isready -h postgres -U postgres -d agent_eval >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 60 ]; then
    docker logs "$PG" >&2
    exit 1
  fi
  sleep 1
done

# The official image can briefly accept connections before completing its
# initialization transition. Require the network-visible probe to stay ready.
sleep 2
if ! docker run --rm --network "$NETWORK" postgres:16-alpine \
  pg_isready -h postgres -U postgres -d agent_eval >/dev/null 2>&1; then
  docker logs "$PG" >&2
  exit 1
fi

docker run -d --name "$BACKEND" --network "$NETWORK" --network-alias backend \
  -p 127.0.0.1:18083:8000 \
  -v "$VOLUME:/data/artifacts" \
  -e AGENT_HOST_GATEWAY=127.0.0.1 \
  -e DB_HOST=postgres \
  -e DB_PORT=5432 \
  -e DB_USER=postgres \
  -e DB_PASSWORD=oa-two-tenant-e2e \
  -e DB_NAME=agent_eval \
  -e AUTH_SECRET_KEY=oa-two-tenant-e2e-auth-key-at-least-32-bytes \
  -e OMNIAGENT_PRODUCT_PLANE_ENABLED=true \
  -e OMNIAGENT_EXECUTION_ENABLED=false \
  -e OMNIAGENT_WORKER_ENABLED=false \
  -e OMNIAGENT_RUNNER=disabled \
  -e OMNIAGENT_ARTIFACT_STORAGE=filesystem \
  -e OMNIAGENT_ARTIFACT_ROOT=/data/artifacts \
  -e OMNIAGENT_ARTIFACT_SCANNER=development \
  -e FEISHU_ENABLED=false \
  "$BACKEND_IMAGE" >/dev/null

attempt=0
until curl -fsS http://127.0.0.1:18083/health >/dev/null 2>&1; do
  if [ "$(docker inspect -f '{{.State.Running}}' "$BACKEND" 2>/dev/null || true)" != true ]; then
    docker logs "$BACKEND" >&2 || true
    echo "backend exited before becoming healthy" >&2
    exit 1
  fi
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 300 ]; then
    docker logs "$BACKEND" >&2
    echo "backend did not become healthy within 300 seconds" >&2
    exit 1
  fi
  sleep 1
done

docker cp "$ROOT/e2e/omniagent-two-tenant/seed.py" "$BACKEND:/tmp/seed.py"
docker exec "$BACKEND" python /tmp/seed.py > "$FIXTURE_FILE"
python3 -c 'import json,sys; json.load(open(sys.argv[1], encoding="utf-8"))' "$FIXTURE_FILE"

docker build -t "$FRONTEND_IMAGE" "$ROOT/frontend" >/dev/null
docker run -d --name "$FRONTEND" --network "$NETWORK" \
  -p 127.0.0.1:18082:80 \
  "$FRONTEND_IMAGE" >/dev/null

attempt=0
until curl -fsS http://127.0.0.1:18082/health >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 60 ]; then
    docker logs "$FRONTEND" >&2
    exit 1
  fi
  sleep 1
done

trap - EXIT INT TERM
printf '%s\n' "OA_TWO_TENANT_E2E_READY url=http://127.0.0.1:18082 fixture=$FIXTURE_FILE"
