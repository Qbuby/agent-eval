#!/usr/bin/env sh
set -eu

IMAGE=${1:?usage: smoke.sh IMAGE}
NAME="agent-eval-analysis-smoke-$$"
INPUT=$(mktemp)
HEALTH_FILE="/tmp/analysis-health-$$"
BASE_URL=

cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  rm -f "$INPUT" "$HEALTH_FILE"
}
trap cleanup EXIT HUP INT TERM

fail() {
  printf '%s\n' "ANALYSIS_RUNTIME_SMOKE_FAILED: $*" >&2
  exit 1
}

if command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON=python
else
  fail "python3 or python is required on the Docker host"
fi

CONFIG=$(docker image inspect "$IMAGE" --format '{{json .Config}}') || \
  fail "could not inspect image metadata"
printf '%s' "$CONFIG" | "$PYTHON" -c '
import json, sys
config = json.load(sys.stdin)
assert config["User"] == "10001:10001", config["User"]
assert config["WorkingDir"] == "/workspace", config["WorkingDir"]
assert config["Entrypoint"][0:3] == ["/opt/runtime/bin/python", "-m", "uvicorn"]
' || fail "image metadata is not hardened"

docker run --detach --name "$NAME" \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --pids-limit 64 \
  --memory 512m \
  --tmpfs /workspace:rw,nosuid,nodev,size=64m,uid=10001,gid=10001,mode=0700 \
  --tmpfs /tmp:rw,nosuid,nodev,size=32m,uid=10001,gid=10001,mode=0700 \
  --publish 127.0.0.1::8888 \
  "$IMAGE" >/dev/null

PORT=$(docker port "$NAME" 8888/tcp | sed -n 's/.*://p')
[ -n "$PORT" ] || fail "Docker did not publish the runtime port"
BASE_URL="http://127.0.0.1:$PORT"

attempt=0
until curl --fail --silent "$BASE_URL/healthz" > "$HEALTH_FILE" 2>/dev/null; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    docker logs "$NAME" >&2 || true
    fail "runtime did not become healthy"
  fi
  sleep 1
done
"$PYTHON" -c 'import json,sys; assert json.load(open(sys.argv[1])) == {"status":"ok"}' \
  "$HEALTH_FILE" || fail "health response is invalid"
rm -f "$HEALTH_FILE"

UID_RESULT=$(curl --fail --silent --show-error \
  --header 'Content-Type: application/json' \
  --data '{"command":"id -u; id -g","timeout":5}' \
  "$BASE_URL/execute")
printf '%s' "$UID_RESULT" | "$PYTHON" -c '
import json,sys
body=json.load(sys.stdin)
assert body["exit_code"] == 0, body
assert body["stdout"].splitlines() == ["10001", "10001"], body
' || fail "runtime process is not UID/GID 10001"

printf 'runtime-smoke-body' > "$INPUT"
curl --fail --silent --show-error \
  --form "file=@$INPUT;filename=/workspace/probe.txt" \
  "$BASE_URL/upload" >/dev/null
[ "$(curl --fail --silent --show-error "$BASE_URL/download//workspace/probe.txt")" = \
  'runtime-smoke-body' ] || fail "upload/download round trip failed"

ENV_RESULT=$(curl --fail --silent --show-error \
  --header 'Content-Type: application/json' \
  --data '{"command":"printf %s \"$SMOKE_ENV\"","timeout":5,"env":{"SMOKE_ENV":"one-command-only"}}' \
  "$BASE_URL/execute")
printf '%s' "$ENV_RESULT" | "$PYTHON" -c '
import json,sys
body=json.load(sys.stdin)
assert body["exit_code"] == 0, body
assert body["stdout"] == "one-command-only", body
' || fail "command environment was not injected"

NEXT_ENV_RESULT=$(curl --fail --silent --show-error \
  --header 'Content-Type: application/json' \
  --data '{"command":"printf %s \"${SMOKE_ENV-unset}\"","timeout":5}' \
  "$BASE_URL/execute")
printf '%s' "$NEXT_ENV_RESULT" | "$PYTHON" -c '
import json,sys
body=json.load(sys.stdin)
assert body["exit_code"] == 0, body
assert body["stdout"] == "unset", body
' || fail "command environment leaked into a later process"

TIMEOUT_RESULT=$(curl --fail --silent --show-error \
  --header 'Content-Type: application/json' \
  --data '{"command":"(sleep 4; printf survived > /workspace/survived) & wait","timeout":1}' \
  "$BASE_URL/execute")
printf '%s' "$TIMEOUT_RESULT" | "$PYTHON" -c '
import json,sys
body=json.load(sys.stdin)
assert body["exit_code"] == 124, body
assert "timed out" in body["stderr"].lower(), body
' || fail "runtime timeout contract failed"
sleep 5
STATUS=$(curl --silent --output /dev/null --write-out '%{http_code}' \
  "$BASE_URL/download//workspace/survived")
[ "$STATUS" = 404 ] || fail "a timed-out child process survived"

ROOT_STATUS=$(curl --silent --output /dev/null --write-out '%{http_code}' \
  "$BASE_URL/download//etc/passwd")
[ "$ROOT_STATUS" = 403 ] || fail "download path escape was not rejected"

printf '%s\n' "ANALYSIS_RUNTIME_SMOKE_OK image=$IMAGE"
