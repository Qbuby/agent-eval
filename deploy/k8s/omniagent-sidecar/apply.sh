#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../../.." && pwd)
CONFIG_DIR="$REPO_ROOT/deploy/omniagent"
TEMPLATE="$SCRIPT_DIR/backend-patch.yaml.tpl"

NAMESPACE=${NAMESPACE:-agent-eval}
DEPLOYMENT_NAME=${DEPLOYMENT_NAME:-backend}
CONFIGMAP_NAME=${CONFIGMAP_NAME:-omniagent-config}
SECRET_NAME=${SECRET_NAME:-omniagent-secret}
OMNIAGENT_MODEL=${OMNIAGENT_MODEL:-gpt-5.6-terra}
OMNIAGENT_BASE_URL=${OMNIAGENT_BASE_URL:-https://kiro.aidong-ai.com/v1}
DB_HOST=${DB_HOST:-postgres}
DB_USER=${DB_USER:-postgres}
DRY_RUN=${DRY_RUN:-0}

: "${OMNIAGENT_IMAGE:?OMNIAGENT_IMAGE is required and must be pullable by the cluster}"

for command in kubectl sed; do
  command -v "$command" >/dev/null 2>&1 || {
    printf 'missing required command: %s\n' "$command" >&2
    exit 1
  }
done

for file in \
  "$CONFIG_DIR/config.yaml" \
  "$CONFIG_DIR/mcp_server.json" \
  "$CONFIG_DIR/overlay/sitecustomize.py" \
  "$CONFIG_DIR/overlay/omniagent_overlay/__init__.py" \
  "$CONFIG_DIR/overlay/omniagent_overlay/axi_bridge.py" \
  "$CONFIG_DIR/overlay/omniagent_overlay/axi_tools.py" \
  "$CONFIG_DIR/prompts/SOUL.md" \
  "$CONFIG_DIR/prompts/GUARDRAILS.md" \
  "$CONFIG_DIR/prompts/AGENTS.md" \
  "$CONFIG_DIR/skills/artifact-handling/SKILL.md" \
  "$CONFIG_DIR/skills/controlled-analysis/SKILL.md" \
  "$CONFIG_DIR/skills/data-investigation/SKILL.md" \
  "$CONFIG_DIR/skills/delegation/SKILL.md" \
  "$CONFIG_DIR/skills/governed-actions/SKILL.md" \
  "$CONFIG_DIR/skills/personal-memory/SKILL.md" \
  "$CONFIG_DIR/skills/scheduled-automation/SKILL.md"; do
  [ -f "$file" ] || { printf 'missing config file: %s\n' "$file" >&2; exit 1; }
done

hash_files() {
  # 只散列文件内容，不把 CI workspace 的绝对路径混入摘要，避免相同配置因
  # runner 路径不同触发无意义滚动更新。调用方传入顺序固定，拼接结果稳定。
  if command -v sha256sum >/dev/null 2>&1; then
    cat "$@" | sha256sum | cut -d ' ' -f 1
  elif command -v shasum >/dev/null 2>&1; then
    cat "$@" | shasum -a 256 | cut -d ' ' -f 1
  else
    printf 'sha256sum or shasum is required\n' >&2
    exit 1
  fi
}

CONFIG_HASH=$(hash_files \
  "$CONFIG_DIR/config.yaml" \
  "$CONFIG_DIR/mcp_server.json" \
  "$CONFIG_DIR/overlay/sitecustomize.py" \
  "$CONFIG_DIR/overlay/omniagent_overlay/__init__.py" \
  "$CONFIG_DIR/overlay/omniagent_overlay/axi_bridge.py" \
  "$CONFIG_DIR/overlay/omniagent_overlay/axi_tools.py" \
  "$CONFIG_DIR/prompts/SOUL.md" \
  "$CONFIG_DIR/prompts/GUARDRAILS.md" \
  "$CONFIG_DIR/prompts/AGENTS.md" \
  "$CONFIG_DIR/skills/artifact-handling/SKILL.md" \
  "$CONFIG_DIR/skills/controlled-analysis/SKILL.md" \
  "$CONFIG_DIR/skills/data-investigation/SKILL.md" \
  "$CONFIG_DIR/skills/delegation/SKILL.md" \
  "$CONFIG_DIR/skills/governed-actions/SKILL.md" \
  "$CONFIG_DIR/skills/personal-memory/SKILL.md" \
  "$CONFIG_DIR/skills/scheduled-automation/SKILL.md")

escape_sed() {
  printf '%s' "$1" | sed 's/[\\&|]/\\&/g'
}

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT INT TERM
PATCH_FILE="$TMP_DIR/backend-patch.yaml"
CONFIGMAP_FILE="$TMP_DIR/configmap.yaml"
SECRET_FILE="$TMP_DIR/secret.yaml"
BASE_FILE="$TMP_DIR/base-deployment.yaml"
MERGED_FILE="$TMP_DIR/merged-deployment.yaml"

sed \
  -e "s|\${NAMESPACE}|$(escape_sed "$NAMESPACE")|g" \
  -e "s|\${DEPLOYMENT_NAME}|$(escape_sed "$DEPLOYMENT_NAME")|g" \
  -e "s|\${CONFIG_HASH}|$(escape_sed "$CONFIG_HASH")|g" \
  -e "s|\${OMNIAGENT_IMAGE}|$(escape_sed "$OMNIAGENT_IMAGE")|g" \
  -e "s|\${OMNIAGENT_MODEL}|$(escape_sed "$OMNIAGENT_MODEL")|g" \
  -e "s|\${OMNIAGENT_BASE_URL}|$(escape_sed "$OMNIAGENT_BASE_URL")|g" \
  -e "s|\${SECRET_NAME}|$(escape_sed "$SECRET_NAME")|g" \
  -e "s|\${DB_HOST}|$(escape_sed "$DB_HOST")|g" \
  -e "s|\${DB_USER}|$(escape_sed "$DB_USER")|g" \
  -e "s|\${CONFIGMAP_NAME}|$(escape_sed "$CONFIGMAP_NAME")|g" \
  "$TEMPLATE" > "$PATCH_FILE"

kubectl create configmap "$CONFIGMAP_NAME" \
  --namespace "$NAMESPACE" \
  --from-file=config.yaml="$CONFIG_DIR/config.yaml" \
  --from-file=mcp_server.json="$CONFIG_DIR/mcp_server.json" \
  --from-file=sitecustomize.py="$CONFIG_DIR/overlay/sitecustomize.py" \
  --from-file=overlay-init.py="$CONFIG_DIR/overlay/omniagent_overlay/__init__.py" \
  --from-file=axi_bridge.py="$CONFIG_DIR/overlay/omniagent_overlay/axi_bridge.py" \
  --from-file=axi_tools.py="$CONFIG_DIR/overlay/omniagent_overlay/axi_tools.py" \
  --from-file=SOUL.md="$CONFIG_DIR/prompts/SOUL.md" \
  --from-file=GUARDRAILS.md="$CONFIG_DIR/prompts/GUARDRAILS.md" \
  --from-file=AGENTS.md="$CONFIG_DIR/prompts/AGENTS.md" \
  --from-file=skill-artifact-handling.md="$CONFIG_DIR/skills/artifact-handling/SKILL.md" \
  --from-file=skill-controlled-analysis.md="$CONFIG_DIR/skills/controlled-analysis/SKILL.md" \
  --from-file=skill-data-investigation.md="$CONFIG_DIR/skills/data-investigation/SKILL.md" \
  --from-file=skill-delegation.md="$CONFIG_DIR/skills/delegation/SKILL.md" \
  --from-file=skill-governed-actions.md="$CONFIG_DIR/skills/governed-actions/SKILL.md" \
  --from-file=skill-personal-memory.md="$CONFIG_DIR/skills/personal-memory/SKILL.md" \
  --from-file=skill-scheduled-automation.md="$CONFIG_DIR/skills/scheduled-automation/SKILL.md" \
  --dry-run=client -o yaml > "$CONFIGMAP_FILE"

if [ -n "${OMNIAGENT_API_KEY:-}" ] || [ -n "${DB_PASSWORD:-}" ]; then
  : "${OMNIAGENT_API_KEY:?OMNIAGENT_API_KEY and DB_PASSWORD must be provided together}"
  : "${DB_PASSWORD:?OMNIAGENT_API_KEY and DB_PASSWORD must be provided together}"
  kubectl create secret generic "$SECRET_NAME" \
    --namespace "$NAMESPACE" \
    --from-literal=OMNIAGENT_API_KEY="$OMNIAGENT_API_KEY" \
    --from-literal=DB_PASSWORD="$DB_PASSWORD" \
    --dry-run=client -o yaml > "$SECRET_FILE"
fi

if [ "$DRY_RUN" = "1" ]; then
  cat > "$BASE_FILE" <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: $DEPLOYMENT_NAME
  namespace: $NAMESPACE
spec:
  selector:
    matchLabels:
      app: backend
  template:
    metadata:
      labels:
        app: backend
    spec:
      initContainers:
        - name: wait-for-postgres
          image: postgres:16-alpine
        - name: db-migrate
          image: example.invalid/backend:test
      containers:
        - name: backend
          image: example.invalid/backend:test
EOF
  kubectl patch --local -f "$BASE_FILE" --type=strategic \
    --patch-file "$PATCH_FILE" -o yaml > "$MERGED_FILE"
  printf 'DRY_RUN_OK namespace=%s deployment=%s image=%s config_sha256=%s\n' \
    "$NAMESPACE" "$DEPLOYMENT_NAME" "$OMNIAGENT_IMAGE" "$CONFIG_HASH"
  printf '%s\n' '--- rendered sidecar patch ---'
  cat "$PATCH_FILE"
  printf '%s\n' '--- merged Deployment fixture ---'
  cat "$MERGED_FILE"
  exit 0
fi

kubectl get deployment "$DEPLOYMENT_NAME" --namespace "$NAMESPACE" >/dev/null
if [ ! -s "$SECRET_FILE" ]; then
  kubectl get secret "$SECRET_NAME" --namespace "$NAMESPACE" >/dev/null || {
    printf 'secret %s is missing; set OMNIAGENT_API_KEY and DB_PASSWORD or create it first\n' "$SECRET_NAME" >&2
    exit 1
  }
fi

kubectl apply -f "$CONFIGMAP_FILE"
[ ! -s "$SECRET_FILE" ] || kubectl apply -f "$SECRET_FILE"
kubectl patch deployment "$DEPLOYMENT_NAME" --namespace "$NAMESPACE" \
  --type=strategic --patch-file "$PATCH_FILE"
kubectl rollout status deployment/"$DEPLOYMENT_NAME" \
  --namespace "$NAMESPACE" --timeout="${ROLLOUT_TIMEOUT:-300s}"

printf 'OmniAgent sidecar applied to %s/%s\n' "$NAMESPACE" "$DEPLOYMENT_NAME"
