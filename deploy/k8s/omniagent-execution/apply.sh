#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MODE=${MODE:-render}
NAMESPACE=${NAMESPACE:-agent-eval}
DEPLOYMENT_NAME=${DEPLOYMENT_NAME:-backend}
CONFIRM_EXECUTION_APPLY=${CONFIRM_EXECUTION_APPLY:-}
ENABLE_AXI_TOOLS=${ENABLE_AXI_TOOLS:-0}
DISABLE_AXI_TOOLS=${DISABLE_AXI_TOOLS:-0}
DISABLE_EXECUTION=${DISABLE_EXECUTION:-0}
AXI_RUNTIME_IMAGE=${AXI_RUNTIME_IMAGE:-}
CONFIRM_AXI_LICENSE_REVIEWED=${CONFIRM_AXI_LICENSE_REVIEWED:-}
EXECUTION_TENANT_ALLOWLIST=${EXECUTION_TENANT_ALLOWLIST:-}
EXECUTION_SECRET_NAME=${EXECUTION_SECRET_NAME:-omniagent-execution-secret}
ORIGINAL_SERVICE_ACCOUNT=${ORIGINAL_SERVICE_ACCOUNT:-}
ROLLBACK_CLEANUP_COMPLETE=0

fail_invalid_name() {
  printf '%s must be a valid lowercase Kubernetes DNS name: %s\n' "$1" "$2" >&2
  exit 1
}

validate_dns_label() {
  label_name=$1
  label_value=$2
  if [ -z "$label_value" ] || [ "${#label_value}" -gt 63 ]; then
    fail_invalid_name "$label_name" "$label_value"
  fi
  case "$label_value" in
    *[!a-z0-9-]*|-*|*-)
      fail_invalid_name "$label_name" "$label_value"
      ;;
  esac
}

validate_dns_subdomain() {
  subdomain_name=$1
  subdomain_value=$2
  if [ -z "$subdomain_value" ] || [ "${#subdomain_value}" -gt 253 ]; then
    fail_invalid_name "$subdomain_name" "$subdomain_value"
  fi
  case "$subdomain_value" in
    *[!a-z0-9.-]*|.*|*.|-*|*-|*..*)
      fail_invalid_name "$subdomain_name" "$subdomain_value"
      ;;
  esac
  saved_ifs=$IFS
  IFS=.
  for subdomain_label in $subdomain_value; do
    validate_dns_label "$subdomain_name" "$subdomain_label"
  done
  IFS=$saved_ifs
}

validate_image_digest() {
  image_name=$1
  image_value=$2
  case "$image_value" in
    *@sha256:????????????????????????????????????????????????????????????????) ;;
    *)
      printf '%s must end with @sha256:<64 lowercase hex characters>\n' "$image_name" >&2
      exit 1
      ;;
  esac
  case "$image_value" in
    @*|*@*@*|*[!A-Za-z0-9._:/@-]*)
      printf '%s contains characters that are unsafe for manifest rendering\n' "$image_name" >&2
      exit 1
      ;;
  esac
  image_digest=${image_value##*@sha256:}
  case "$image_digest" in
    *[!0-9a-f]*)
      printf '%s digest must contain lowercase hexadecimal characters only\n' "$image_name" >&2
      exit 1
      ;;
  esac
}

validate_restoration_target() {
  restoration_name=$1
  restoration_value=$2
  validate_dns_subdomain "$restoration_name" "$restoration_value"
  if [ "$restoration_value" = "omniagent-executor" ]; then
    printf '%s must not be omniagent-executor\n' "$restoration_name" >&2
    exit 1
  fi
}

validate_dns_label NAMESPACE "$NAMESPACE"
validate_dns_subdomain DEPLOYMENT_NAME "$DEPLOYMENT_NAME"
validate_dns_subdomain EXECUTION_SECRET_NAME "$EXECUTION_SECRET_NAME"

case "$MODE" in
  render|server-dry-run|apply) ;;
  *)
    printf 'unsupported MODE: %s\n' "$MODE" >&2
    exit 1
    ;;
esac

case "$ENABLE_AXI_TOOLS" in
  0|1) ;;
  *)
    printf '%s\n' 'ENABLE_AXI_TOOLS must be 0 or 1' >&2
    exit 1
    ;;
esac
case "$DISABLE_AXI_TOOLS" in
  0|1) ;;
  *)
    printf '%s\n' 'DISABLE_AXI_TOOLS must be 0 or 1' >&2
    exit 1
    ;;
esac
case "$DISABLE_EXECUTION" in
  0|1) ;;
  *)
    printf '%s\n' 'DISABLE_EXECUTION must be 0 or 1' >&2
    exit 1
    ;;
esac
if [ "$DISABLE_EXECUTION" = "1" ] && \
   { [ "$ENABLE_AXI_TOOLS" = "1" ] || [ "$DISABLE_AXI_TOOLS" = "1" ]; }; then
  printf '%s\n' 'DISABLE_EXECUTION cannot be combined with Axi enable/disable modes' >&2
  exit 1
fi
if [ "$ENABLE_AXI_TOOLS" = "1" ] && [ "$DISABLE_AXI_TOOLS" = "1" ]; then
  printf '%s\n' 'ENABLE_AXI_TOOLS and DISABLE_AXI_TOOLS cannot both be 1' >&2
  exit 1
fi

ROLLBACK_ONLY=0
if [ "$DISABLE_AXI_TOOLS" = "1" ] || [ "$DISABLE_EXECUTION" = "1" ]; then
  ROLLBACK_ONLY=1
fi

if [ "$ROLLBACK_ONLY" = "0" ]; then
  if [ -z "$EXECUTION_TENANT_ALLOWLIST" ]; then
    printf '%s\n' 'EXECUTION_TENANT_ALLOWLIST is required and must contain tenant UUIDs' >&2
    exit 1
  fi
  old_ifs=$IFS
  IFS=,
  for tenant_id in $EXECUTION_TENANT_ALLOWLIST; do
    case "$tenant_id" in
      ????????-????-????-????-????????????) ;;
      *)
        printf 'invalid tenant UUID in EXECUTION_TENANT_ALLOWLIST: %s\n' "$tenant_id" >&2
        exit 1
        ;;
    esac
    compact=$(printf '%s' "$tenant_id" | tr -d '-')
    case "$compact" in
      *[!0-9a-fA-F]*)
        printf 'invalid tenant UUID in EXECUTION_TENANT_ALLOWLIST: %s\n' "$tenant_id" >&2
        exit 1
        ;;
    esac
  done
  IFS=$old_ifs
  : "${ANALYSIS_RUNTIME_IMAGE:?ANALYSIS_RUNTIME_IMAGE is required and must use an immutable digest}"
  validate_image_digest ANALYSIS_RUNTIME_IMAGE "$ANALYSIS_RUNTIME_IMAGE"
fi

if [ "$MODE" = "render" ] && { [ "$ROLLBACK_ONLY" = "0" ] || [ "$DISABLE_EXECUTION" = "1" ]; }; then
  if [ -z "$ORIGINAL_SERVICE_ACCOUNT" ]; then
    printf '%s\n' 'ORIGINAL_SERVICE_ACCOUNT is required for offline execution enable/disable rendering' >&2
    exit 1
  fi
  validate_restoration_target ORIGINAL_SERVICE_ACCOUNT "$ORIGINAL_SERVICE_ACCOUNT"
fi

if [ "$ENABLE_AXI_TOOLS" = "1" ]; then
  if [ "$CONFIRM_AXI_LICENSE_REVIEWED" != "axi-license-reviewed" ]; then
    printf '%s\n' 'set CONFIRM_AXI_LICENSE_REVIEWED=axi-license-reviewed to render or enable Axi tools' >&2
    exit 1
  fi
  validate_image_digest AXI_RUNTIME_IMAGE "$AXI_RUNTIME_IMAGE"
fi

for command in sed mktemp; do
  command -v "$command" >/dev/null 2>&1 || {
    printf 'missing required command: %s\n' "$command" >&2
    exit 1
  }
done
if [ "$MODE" != "render" ]; then
  command -v kubectl >/dev/null 2>&1 || {
    printf '%s\n' 'kubectl is required for server-dry-run and apply modes' >&2
    exit 1
  }
fi
if [ "$MODE" = "apply" ] && [ "$CONFIRM_EXECUTION_APPLY" != "apply-omniagent-execution" ]; then
  printf '%s\n' 'set CONFIRM_EXECUTION_APPLY=apply-omniagent-execution to modify the cluster' >&2
  exit 1
fi

escape_sed() {
  printf '%s' "$1" | sed 's/[\\&|]/\\&/g'
}

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT INT TERM
TEMPLATE_FILE="$TMP_DIR/sandbox-template.yaml"
RBAC_FILE="$TMP_DIR/namespace-rbac.yaml"
INTERNAL_SERVICE_FILE="$TMP_DIR/internal-service.yaml"
PATCH_FILE="$TMP_DIR/backend-executor-patch.yaml"
STOP_PATCH_FILE="$TMP_DIR/backend-executor-stop-patch.yaml"
DISABLE_PATCH_FILE="$TMP_DIR/backend-executor-disable-patch.yaml"
AXI_PATCH_FILE="$TMP_DIR/axi-tools-patch.yaml"
AXI_DISABLE_PATCH_FILE="$TMP_DIR/axi-tools-disable-patch.yaml"
AXI_TEMPLATE_FILE="$TMP_DIR/axi-sandbox-template.yaml"
RESOURCES_FILE="$TMP_DIR/resources.yaml"

if [ "$ROLLBACK_ONLY" = "0" ]; then
  sed -e "s|\${NAMESPACE}|$(escape_sed "$NAMESPACE")|g" \
    "$SCRIPT_DIR/00-namespace-rbac.yaml" > "$RBAC_FILE"
  sed -e "s|\${NAMESPACE}|$(escape_sed "$NAMESPACE")|g" \
    "$SCRIPT_DIR/10-internal-service.yaml" > "$INTERNAL_SERVICE_FILE"
  sed \
    -e "s|\${NAMESPACE}|$(escape_sed "$NAMESPACE")|g" \
    -e "s|\${ANALYSIS_RUNTIME_IMAGE}|$(escape_sed "$ANALYSIS_RUNTIME_IMAGE")|g" \
    "$SCRIPT_DIR/20-sandbox-template.yaml.tpl" > "$TEMPLATE_FILE"
  sed \
    -e "s|\${NAMESPACE}|$(escape_sed "$NAMESPACE")|g" \
    -e "s|\${DEPLOYMENT_NAME}|$(escape_sed "$DEPLOYMENT_NAME")|g" \
    -e "s|\${EXECUTION_TENANT_ALLOWLIST}|$(escape_sed "$EXECUTION_TENANT_ALLOWLIST")|g" \
    -e "s|\${EXECUTION_SECRET_NAME}|$(escape_sed "$EXECUTION_SECRET_NAME")|g" \
    -e "s|\${ORIGINAL_SERVICE_ACCOUNT}|$(escape_sed "$ORIGINAL_SERVICE_ACCOUNT")|g" \
    "$SCRIPT_DIR/backend-executor-patch.yaml.tpl" > "$PATCH_FILE"
fi

if [ "$ENABLE_AXI_TOOLS" = "1" ]; then
  sed \
    -e "s|\${NAMESPACE}|$(escape_sed "$NAMESPACE")|g" \
    -e "s|\${AXI_RUNTIME_IMAGE}|$(escape_sed "$AXI_RUNTIME_IMAGE")|g" \
    "$SCRIPT_DIR/30-axi-sandbox-template.yaml.tpl" > "$AXI_TEMPLATE_FILE"
  sed \
    -e "s|\${NAMESPACE}|$(escape_sed "$NAMESPACE")|g" \
    -e "s|\${DEPLOYMENT_NAME}|$(escape_sed "$DEPLOYMENT_NAME")|g" \
    "$SCRIPT_DIR/axi-tools-patch.yaml.tpl" > "$AXI_PATCH_FILE"
fi
if [ "$DISABLE_AXI_TOOLS" = "1" ]; then
  sed \
    -e "s|\${NAMESPACE}|$(escape_sed "$NAMESPACE")|g" \
    -e "s|\${DEPLOYMENT_NAME}|$(escape_sed "$DEPLOYMENT_NAME")|g" \
    "$SCRIPT_DIR/axi-tools-disable-patch.yaml.tpl" > "$AXI_DISABLE_PATCH_FILE"
fi
if [ "$DISABLE_EXECUTION" = "1" ]; then
  sed \
    -e "s|\${NAMESPACE}|$(escape_sed "$NAMESPACE")|g" \
    -e "s|\${DEPLOYMENT_NAME}|$(escape_sed "$DEPLOYMENT_NAME")|g" \
    "$SCRIPT_DIR/backend-executor-stop-patch.yaml.tpl" > "$STOP_PATCH_FILE"
  sed \
    -e "s|\${NAMESPACE}|$(escape_sed "$NAMESPACE")|g" \
    -e "s|\${DEPLOYMENT_NAME}|$(escape_sed "$DEPLOYMENT_NAME")|g" \
    -e "s|\${ORIGINAL_SERVICE_ACCOUNT}|$(escape_sed "$ORIGINAL_SERVICE_ACCOUNT")|g" \
    "$SCRIPT_DIR/backend-executor-disable-patch.yaml.tpl" > "$DISABLE_PATCH_FILE"
  sed \
    -e "s|\${NAMESPACE}|$(escape_sed "$NAMESPACE")|g" \
    -e "s|\${DEPLOYMENT_NAME}|$(escape_sed "$DEPLOYMENT_NAME")|g" \
    "$SCRIPT_DIR/axi-tools-disable-patch.yaml.tpl" > "$AXI_DISABLE_PATCH_FILE"
fi

if [ "$ROLLBACK_ONLY" = "0" ]; then
  cat "$RBAC_FILE" > "$RESOURCES_FILE"
  printf '\n---\n' >> "$RESOURCES_FILE"
  cat "$INTERNAL_SERVICE_FILE" >> "$RESOURCES_FILE"
  printf '\n---\n' >> "$RESOURCES_FILE"
  cat "$TEMPLATE_FILE" >> "$RESOURCES_FILE"
  if [ "$ENABLE_AXI_TOOLS" = "1" ]; then
    printf '\n---\n' >> "$RESOURCES_FILE"
    cat "$AXI_TEMPLATE_FILE" >> "$RESOURCES_FILE"
  fi
fi

if [ "$MODE" = "render" ]; then
  if [ "$ROLLBACK_ONLY" = "0" ]; then
    printf '%s\n' '# --- resources (kubectl apply) ---'
    cat "$RESOURCES_FILE"
    printf '\n%s\n' '# --- backend identity (kubectl strategic merge patch) ---'
    cat "$PATCH_FILE"
  fi
  if [ "$ENABLE_AXI_TOOLS" = "1" ]; then
    printf '\n%s\n' '# --- OmniAgent Axi tools (kubectl strategic merge patch) ---'
    cat "$AXI_PATCH_FILE"
  fi
  if [ "$DISABLE_AXI_TOOLS" = "1" ]; then
    printf '\n%s\n' '# --- OmniAgent Axi tools rollback (kubectl strategic merge patch) ---'
    cat "$AXI_DISABLE_PATCH_FILE"
  fi
  if [ "$DISABLE_EXECUTION" = "1" ]; then
    printf '\n%s\n' '# --- execution stop phase (kubectl strategic merge patch) ---'
    cat "$STOP_PATCH_FILE"
    printf '\n%s\n' '# --- execution cleanup phase (kubectl strategic merge patch) ---'
    cat "$DISABLE_PATCH_FILE"
    printf '\n%s\n' '# --- optional Axi cleanup (kubectl strategic merge patch) ---'
    cat "$AXI_DISABLE_PATCH_FILE"
  fi
  exit 0
fi

preflight_deployment() {
  pod_app=$(kubectl get deployment "$DEPLOYMENT_NAME" --namespace "$NAMESPACE" \
    -o 'jsonpath={.spec.template.metadata.labels.app}')
  if [ "$pod_app" != "backend" ]; then
    printf '%s\n' 'target Deployment Pod template must have label app=backend' >&2
    exit 1
  fi
  containers=$(kubectl get deployment "$DEPLOYMENT_NAME" --namespace "$NAMESPACE" \
    -o 'jsonpath={range .spec.template.spec.containers[*]}{.name}{"\n"}{end}')
  case "
$containers
" in
    *"
backend
"*) ;;
    *)
      printf '%s\n' 'target Deployment does not contain the backend container' >&2
      exit 1
      ;;
  esac
  if [ "$ENABLE_AXI_TOOLS" = "1" ] || [ "$DISABLE_AXI_TOOLS" = "1" ]; then
    case "
$containers
" in
      *"
omniagent
"*) ;;
      *)
        printf '%s\n' 'target Deployment does not contain the omniagent container' >&2
        exit 1
        ;;
    esac
  fi
  if [ "$ROLLBACK_ONLY" = "0" ]; then
    execution_secret=$(kubectl get secret "$EXECUTION_SECRET_NAME" --namespace "$NAMESPACE" \
      -o 'jsonpath={.data.OMNIAGENT_EXECUTION_SECRET_KEY}')
    if [ -z "$execution_secret" ]; then
      printf 'Secret %s/%s is missing data.OMNIAGENT_EXECUTION_SECRET_KEY\n' \
        "$NAMESPACE" "$EXECUTION_SECRET_NAME" >&2
      exit 1
    fi
  fi

  current_service_account=$(kubectl get deployment "$DEPLOYMENT_NAME" --namespace "$NAMESPACE" \
    -o 'jsonpath={.spec.template.spec.serviceAccountName}')
  recorded_service_account=$(kubectl get deployment "$DEPLOYMENT_NAME" --namespace "$NAMESPACE" \
    -o "jsonpath={.spec.template.metadata.annotations['agent-eval.aidong.ai/omniagent-previous-service-account']}")
  restored_service_account=$(kubectl get deployment "$DEPLOYMENT_NAME" --namespace "$NAMESPACE" \
    -o "jsonpath={.spec.template.metadata.annotations['agent-eval.aidong.ai/omniagent-restored-service-account']}")
  execution_state=$(kubectl get deployment "$DEPLOYMENT_NAME" --namespace "$NAMESPACE" \
    -o "jsonpath={.spec.template.metadata.annotations['agent-eval.aidong.ai/omniagent-execution']}")

  if [ -n "$current_service_account" ]; then
    validate_dns_subdomain current_service_account "$current_service_account"
  fi
  if [ -n "$recorded_service_account" ]; then
    validate_restoration_target recorded_service_account "$recorded_service_account"
  fi
  if [ -n "$restored_service_account" ]; then
    validate_restoration_target restored_service_account "$restored_service_account"
  fi

  if [ "$DISABLE_EXECUTION" = "1" ]; then
    if [ "$current_service_account" = "omniagent-executor" ]; then
      if [ -z "$recorded_service_account" ]; then
        printf '%s\n' 'execution rollback cannot restore ServiceAccount: previous identity annotation is missing' >&2
        exit 1
      fi
      case "$execution_state" in
        enabled|stopping) ;;
        *)
          printf '%s\n' 'execution rollback requires an enabled or stopping execution annotation' >&2
          exit 1
          ;;
      esac
      if [ -n "$restored_service_account" ]; then
        printf '%s\n' 'execution rollback found a stale restored ServiceAccount annotation' >&2
        exit 1
      fi
      ORIGINAL_SERVICE_ACCOUNT=$recorded_service_account
    elif [ "$execution_state" = "disabled" ] && \
         [ -z "$recorded_service_account" ] && \
         [ -n "$restored_service_account" ] && \
         [ "$current_service_account" = "$restored_service_account" ]; then
      # Cleanup may have succeeded before the caller lost the final rollout response.
      # The retained restoration marker proves which identity cleanup selected.
      ROLLBACK_CLEANUP_COMPLETE=1
      ORIGINAL_SERVICE_ACCOUNT=$restored_service_account
    else
      printf '%s\n' 'execution rollback state is inconsistent with an enabled or completed rollout' >&2
      exit 1
    fi
  elif [ "$ROLLBACK_ONLY" = "0" ]; then
    if [ "$current_service_account" = "omniagent-executor" ]; then
      if [ -z "$recorded_service_account" ]; then
        printf '%s\n' 'execution is already using omniagent-executor but previous identity annotation is missing' >&2
        exit 1
      fi
      if [ "$execution_state" != "enabled" ] || [ -n "$restored_service_account" ]; then
        printf '%s\n' 'execution enable state is inconsistent with the executor identity' >&2
        exit 1
      fi
      ORIGINAL_SERVICE_ACCOUNT=$recorded_service_account
    else
      if [ -n "$recorded_service_account" ]; then
        printf '%s\n' 'stale previous ServiceAccount annotation must be removed before enabling execution' >&2
        exit 1
      fi
      current_service_account=${current_service_account:-default}
      validate_dns_subdomain current_service_account "$current_service_account"
      if [ -z "$execution_state" ] && [ -z "$restored_service_account" ]; then
        :
      elif [ "$execution_state" = "disabled" ] && \
           [ "$restored_service_account" = "$current_service_account" ]; then
        :
      else
        printf '%s\n' 'execution enable state is inconsistent with an initial or completed cleanup state' >&2
        exit 1
      fi
      ORIGINAL_SERVICE_ACCOUNT=$current_service_account
    fi
  fi

  if [ "$ROLLBACK_ONLY" = "0" ]; then
    sed \
      -e "s|\${NAMESPACE}|$(escape_sed "$NAMESPACE")|g" \
      -e "s|\${DEPLOYMENT_NAME}|$(escape_sed "$DEPLOYMENT_NAME")|g" \
      -e "s|\${EXECUTION_TENANT_ALLOWLIST}|$(escape_sed "$EXECUTION_TENANT_ALLOWLIST")|g" \
      -e "s|\${EXECUTION_SECRET_NAME}|$(escape_sed "$EXECUTION_SECRET_NAME")|g" \
      -e "s|\${ORIGINAL_SERVICE_ACCOUNT}|$(escape_sed "$ORIGINAL_SERVICE_ACCOUNT")|g" \
      "$SCRIPT_DIR/backend-executor-patch.yaml.tpl" > "$PATCH_FILE"
  fi
  if [ "$DISABLE_EXECUTION" = "1" ] && [ "$ROLLBACK_CLEANUP_COMPLETE" = "0" ]; then
    sed \
      -e "s|\${NAMESPACE}|$(escape_sed "$NAMESPACE")|g" \
      -e "s|\${DEPLOYMENT_NAME}|$(escape_sed "$DEPLOYMENT_NAME")|g" \
      -e "s|\${ORIGINAL_SERVICE_ACCOUNT}|$(escape_sed "$ORIGINAL_SERVICE_ACCOUNT")|g" \
      "$SCRIPT_DIR/backend-executor-disable-patch.yaml.tpl" > "$DISABLE_PATCH_FILE"
  fi
}

# Every connected mode validates the target before its first apply or patch.
preflight_deployment

if [ "$MODE" = "server-dry-run" ]; then
  if [ "$ROLLBACK_ONLY" = "0" ]; then
    kubectl apply --dry-run=server -f "$RESOURCES_FILE"
    kubectl patch deployment "$DEPLOYMENT_NAME" --namespace "$NAMESPACE" \
      --type=strategic --patch-file "$PATCH_FILE" --dry-run=server -o yaml >/dev/null
  fi
  if [ "$ENABLE_AXI_TOOLS" = "1" ]; then
    kubectl patch deployment "$DEPLOYMENT_NAME" --namespace "$NAMESPACE" \
      --type=strategic --patch-file "$AXI_PATCH_FILE" --dry-run=server -o yaml >/dev/null
  fi
  if [ "$DISABLE_AXI_TOOLS" = "1" ]; then
    kubectl patch deployment "$DEPLOYMENT_NAME" --namespace "$NAMESPACE" \
      --type=strategic --patch-file "$AXI_DISABLE_PATCH_FILE" --dry-run=server -o yaml >/dev/null
  fi
  if [ "$DISABLE_EXECUTION" = "1" ] && [ "$ROLLBACK_CLEANUP_COMPLETE" = "0" ]; then
    kubectl patch deployment "$DEPLOYMENT_NAME" --namespace "$NAMESPACE" \
      --type=strategic --patch-file "$STOP_PATCH_FILE" --dry-run=server -o yaml >/dev/null
    case "
$containers
" in
      *"
omniagent
"*)
        kubectl patch deployment "$DEPLOYMENT_NAME" --namespace "$NAMESPACE" \
          --type=strategic --patch-file "$AXI_DISABLE_PATCH_FILE" --dry-run=server -o yaml >/dev/null
        ;;
    esac
    kubectl patch deployment "$DEPLOYMENT_NAME" --namespace "$NAMESPACE" \
      --type=strategic --patch-file "$DISABLE_PATCH_FILE" --dry-run=server -o yaml >/dev/null
  fi
  printf 'SERVER_DRY_RUN_OK namespace=%s deployment=%s\n' \
    "$NAMESPACE" "$DEPLOYMENT_NAME"
  exit 0
fi

if [ "$ROLLBACK_ONLY" = "0" ]; then
  kubectl apply -f "$RESOURCES_FILE"
  kubectl patch deployment "$DEPLOYMENT_NAME" --namespace "$NAMESPACE" \
    --type=strategic --patch-file "$PATCH_FILE"
fi
if [ "$ENABLE_AXI_TOOLS" = "1" ]; then
  kubectl patch deployment "$DEPLOYMENT_NAME" --namespace "$NAMESPACE" \
    --type=strategic --patch-file "$AXI_PATCH_FILE"
fi
if [ "$DISABLE_AXI_TOOLS" = "1" ]; then
  kubectl patch deployment "$DEPLOYMENT_NAME" --namespace "$NAMESPACE" \
    --type=strategic --patch-file "$AXI_DISABLE_PATCH_FILE"
fi
if [ "$DISABLE_EXECUTION" = "1" ] && [ "$ROLLBACK_CLEANUP_COMPLETE" = "0" ]; then
  # Phase one rolls the old worker out with token minting and job claiming disabled.
  kubectl patch deployment "$DEPLOYMENT_NAME" --namespace "$NAMESPACE" \
    --type=strategic --patch-file "$STOP_PATCH_FILE"
  kubectl rollout status deployment/"$DEPLOYMENT_NAME" --namespace "$NAMESPACE" \
    --timeout="${ROLLOUT_TIMEOUT:-300s}"
  case "
$containers
" in
    *"
omniagent
"*)
      kubectl patch deployment "$DEPLOYMENT_NAME" --namespace "$NAMESPACE" \
        --type=strategic --patch-file "$AXI_DISABLE_PATCH_FILE"
      ;;
  esac
  # Phase two removes the dormant execution configuration after the stop rollout.
  kubectl patch deployment "$DEPLOYMENT_NAME" --namespace "$NAMESPACE" \
    --type=strategic --patch-file "$DISABLE_PATCH_FILE"
fi
kubectl rollout status deployment/"$DEPLOYMENT_NAME" --namespace "$NAMESPACE" \
  --timeout="${ROLLOUT_TIMEOUT:-300s}"
printf 'OmniAgent execution resources applied to %s/%s\n' "$NAMESPACE" "$DEPLOYMENT_NAME"
