#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
PREFIX=oa-two-tenant-e2e
docker rm -f "$PREFIX-frontend" "$PREFIX-backend" "$PREFIX-postgres" >/dev/null 2>&1 || true
docker network rm "$PREFIX-network" >/dev/null 2>&1 || true
docker volume rm "$PREFIX-artifacts" >/dev/null 2>&1 || true
docker image rm agent-eval-frontend:oa-two-tenant-e2e >/dev/null 2>&1 || true
rm -f "$ROOT/.codex_tmp/omniagent-two-tenant-fixture.json"
printf '%s\n' 'OA_TWO_TENANT_E2E_STOPPED'
