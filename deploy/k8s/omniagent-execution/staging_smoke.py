#!/usr/bin/env python3
"""Fail-closed staging acceptance for the OmniAgent Kubernetes execution plane."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn


SCRIPT_DIR = Path(__file__).resolve().parent
APPLY_SCRIPT = SCRIPT_DIR / "apply.sh"
CLAIM_GROUP = "extensions.agents.x-k8s.io"
CLAIM_VERSION = "v1alpha1"
SANDBOX_GROUP = "agents.x-k8s.io"
LIVE_CONFIRMATION = "create-omniagent-staging-smoke-claims"
MAX_LIVE_CLAIM_TTL_SECONDS = 35 * 60
TTL_EXPIRY_SECONDS = 15
DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
IMAGE_DIGEST = re.compile(
    r"^[A-Za-z0-9._:/-]+@sha256:[0-9a-f]{64}$"
)


class SmokeFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    namespace: str
    sandbox_namespace: str
    deployment: str
    template: str
    service_account: str
    tenant_id: str
    analysis_image: str
    timeout: int
    live: bool
    confirmation: str


def _fail(message: str) -> NoReturn:
    raise SmokeFailure(message)


def _validate_dns_name(name: str, value: str, *, subdomain: bool = False) -> None:
    maximum = 253 if subdomain else 63
    labels = value.split(".") if subdomain else [value]
    if (
        not value
        or len(value) > maximum
        or any(not label or len(label) > 63 or not DNS_LABEL.fullmatch(label) for label in labels)
    ):
        _fail(f"{name} must be a lowercase Kubernetes DNS name")


def _validate_config(config: Config) -> None:
    _validate_dns_name("namespace", config.namespace)
    _validate_dns_name("sandbox namespace", config.sandbox_namespace)
    _validate_dns_name("deployment", config.deployment, subdomain=True)
    _validate_dns_name("template", config.template, subdomain=True)
    _validate_dns_name("service account", config.service_account, subdomain=True)
    try:
        uuid.UUID(config.tenant_id)
    except (ValueError, AttributeError) as exc:
        raise SmokeFailure("tenant ID must be a UUID") from exc
    if not IMAGE_DIGEST.fullmatch(config.analysis_image):
        _fail("analysis image must be an immutable lowercase sha256 reference")


def _run(
    command: list[str],
    *,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=SCRIPT_DIR.parents[2],
        input=input_text,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        _fail(f"command failed ({' '.join(command)}): {detail}")
    return result


def _kubectl(*arguments: str, input_text: str | None = None) -> str:
    return _run(["kubectl", *arguments], input_text=input_text).stdout.strip()


def _kubectl_json(*arguments: str) -> dict[str, Any]:
    raw = _kubectl(*arguments, "-o", "json")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SmokeFailure("kubectl returned invalid JSON") from exc
    if not isinstance(value, dict):
        _fail("kubectl JSON response must be an object")
    return value


def _expect_can_i(expected: bool, *arguments: str) -> None:
    result = _run(
        ["kubectl", "auth", "can-i", *arguments],
        check=False,
    )
    actual = result.stdout.strip().lower()
    if actual not in {"yes", "no"}:
        detail = (result.stderr or result.stdout).strip()
        _fail(f"unexpected kubectl auth can-i response: {detail}")
    valid_return_codes = {0} if actual == "yes" else {0, 1}
    if result.returncode not in valid_return_codes:
        detail = (result.stderr or result.stdout).strip()
        _fail(f"kubectl auth can-i failed: {detail}")
    if (actual == "yes") != expected:
        _fail(
            f"RBAC expectation failed for {' '.join(arguments)}: "
            f"expected {'yes' if expected else 'no'}, got {actual}"
        )


def _condition_ready(resource: dict[str, Any]) -> bool:
    conditions = (resource.get("status") or {}).get("conditions") or []
    return any(
        item.get("type") == "Ready" and item.get("status") == "True"
        for item in conditions
        if isinstance(item, dict)
    )


def _wait_json(
    resource: str,
    name: str,
    namespace: str,
    predicate,
    timeout: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        result = _run(
            [
                "kubectl",
                "get",
                resource,
                name,
                "--namespace",
                namespace,
                "-o",
                "json",
            ],
            check=False,
        )
        if result.returncode == 0:
            try:
                value = json.loads(result.stdout)
            except json.JSONDecodeError:
                value = None
            if isinstance(value, dict):
                last = value
                if predicate(value):
                    return value
        time.sleep(2)
    detail = json.dumps(last.get("status", {}), ensure_ascii=False)
    _fail(f"timed out waiting for {resource}/{name}: {detail}")


def _wait_absent(resource: str, name: str, namespace: str, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = _run(
            [
                "kubectl",
                "get",
                resource,
                name,
                "--namespace",
                namespace,
                "--ignore-not-found",
                "-o",
                "name",
            ],
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            _fail(f"failed while waiting for {resource}/{name} deletion: {detail}")
        if not result.stdout.strip():
            return
        time.sleep(2)
    _fail(f"timed out waiting for {resource}/{name} deletion")


def _validate_template_and_service(
    config: Config,
    template: dict[str, Any],
    service: dict[str, Any],
) -> None:
    spec = template.get("spec") or {}
    pod = (spec.get("podTemplate") or {}).get("spec") or {}
    containers = pod.get("containers") or []
    if spec.get("service") is not True:
        _fail("SandboxTemplate must expose a service")
    if spec.get("envVarsInjectionPolicy") != "Disallowed":
        _fail("SandboxTemplate must disallow claim environment injection")
    if spec.get("networkPolicyManagement") != "Managed":
        _fail("SandboxTemplate NetworkPolicy must be managed")
    if pod.get("automountServiceAccountToken") is not False:
        _fail("SandboxTemplate must disable ServiceAccount token mounting")
    if not pod.get("securityContext", {}).get("runAsNonRoot"):
        _fail("SandboxTemplate must run as non-root")
    if len(containers) != 1:
        _fail("SandboxTemplate must contain exactly one runtime container")
    container = containers[0]
    security = container.get("securityContext") or {}
    if security.get("allowPrivilegeEscalation") is not False:
        _fail("runtime must disable privilege escalation")
    if security.get("readOnlyRootFilesystem") is not True:
        _fail("runtime root filesystem must be read-only")
    if (security.get("capabilities") or {}).get("drop") != ["ALL"]:
        _fail("runtime must drop all Linux capabilities")
    if container.get("image") != config.analysis_image:
        _fail("SandboxTemplate runtime image does not match the reviewed digest")

    policy = spec.get("networkPolicy") or {}
    ingress = policy.get("ingress") or []
    expected_ingress = {
        (config.sandbox_namespace, (("app", "sandbox-router"),), 8888),
        (config.namespace, (("app", "backend"),), 8888),
    }
    actual_ingress = {
        (
            (target.get("namespaceSelector") or {}).get("matchLabels", {}).get(
                "kubernetes.io/metadata.name"
            ),
            tuple(
                sorted(
                    (target.get("podSelector") or {})
                    .get("matchLabels", {})
                    .items()
                )
            ),
            port.get("port"),
        )
        for rule in ingress
        for target in rule.get("from", [])
        for port in rule.get("ports", [])
        if port.get("protocol", "TCP") == "TCP"
    }
    if actual_ingress != expected_ingress:
        _fail("SandboxTemplate ingress is not limited to router and backend")

    egress = policy.get("egress") or []
    actual_egress = {
        (
            (target.get("namespaceSelector") or {}).get("matchLabels", {}).get(
                "kubernetes.io/metadata.name"
            ),
            tuple(
                sorted(
                    (target.get("podSelector") or {})
                    .get("matchLabels", {})
                    .items()
                )
            ),
            port.get("protocol", "TCP"),
            port.get("port"),
        )
        for rule in egress
        for target in rule.get("to", [])
        for port in rule.get("ports", [])
    }
    expected_egress = {
        ("kube-system", (("k8s-app", "kube-dns"),), "UDP", 53),
        ("kube-system", (("k8s-app", "kube-dns"),), "TCP", 53),
        (config.namespace, (("app", "backend"),), "TCP", 8000),
    }
    if actual_egress != expected_egress:
        _fail("SandboxTemplate egress is not limited to DNS and backend")
    if any("ipBlock" in target for rule in egress for target in rule.get("to", [])):
        _fail("SandboxTemplate must not allow arbitrary IP blocks")

    service_spec = service.get("spec") or {}
    if service_spec.get("selector") != {"app": "backend"}:
        _fail("internal Service must select only backend Pods")
    service_ports = service_spec.get("ports") or []
    if service_ports != [
        {"name": "http", "port": 8000, "protocol": "TCP", "targetPort": 8000}
    ]:
        _fail("internal Service must expose only backend TCP port 8000")


def _validate_warm_pool(config: Config, warm_pool: dict[str, Any]) -> None:
    spec = warm_pool.get("spec") or {}
    if spec.get("replicas") != 0:
        _fail("SandboxWarmPool must remain at zero replicas before rollout approval")
    if (spec.get("sandboxTemplateRef") or {}).get("name") != config.template:
        _fail("SandboxWarmPool must reference the reviewed SandboxTemplate")
    if (spec.get("updateStrategy") or {}).get("type") != "Recreate":
        _fail("SandboxWarmPool must use the Recreate update strategy")


def _read_only_acceptance(config: Config) -> None:
    context = _kubectl("config", "current-context")
    if not context:
        _fail("kubectl current-context is empty")

    required_resources = {
        "sandboxclaims.extensions.agents.x-k8s.io",
        "sandboxtemplates.extensions.agents.x-k8s.io",
        "sandboxwarmpools.extensions.agents.x-k8s.io",
        "sandboxes.agents.x-k8s.io",
    }
    available = set(_kubectl("api-resources", "-o", "name").splitlines())
    missing = sorted(required_resources - available)
    if missing:
        _fail(f"required Kubernetes resources are missing: {', '.join(missing)}")

    env = {
        **os.environ,
        "MODE": "server-dry-run",
        "NAMESPACE": config.namespace,
        "DEPLOYMENT_NAME": config.deployment,
        "EXECUTION_TENANT_ALLOWLIST": config.tenant_id,
        "ANALYSIS_RUNTIME_IMAGE": config.analysis_image,
    }
    dry_run = _run(["sh", str(APPLY_SCRIPT)], env=env)
    if "SERVER_DRY_RUN_OK" not in dry_run.stdout:
        _fail("execution apply script did not confirm server-side dry-run")

    subject = f"system:serviceaccount:{config.namespace}:{config.service_account}"
    intended = ["--as", subject, "--namespace", config.sandbox_namespace]
    for verb in ("get", "list", "watch", "create", "delete"):
        _expect_can_i(True, verb, f"sandboxclaims.{CLAIM_GROUP}", *intended)
    for verb in ("get", "list", "watch"):
        _expect_can_i(True, verb, f"sandboxtemplates.{CLAIM_GROUP}", *intended)
        _expect_can_i(True, verb, f"sandboxes.{SANDBOX_GROUP}", *intended)
    _expect_can_i(False, "create", "pods", *intended)
    _expect_can_i(False, "get", "secrets", *intended)
    _expect_can_i(
        False,
        "create",
        f"sandboxclaims.{CLAIM_GROUP}",
        "--as",
        subject,
        "--namespace",
        "default",
    )

    template = _kubectl_json(
        "get",
        "sandboxtemplate",
        config.template,
        "--namespace",
        config.sandbox_namespace,
    )
    service = _kubectl_json(
        "get",
        "service",
        "agent-eval-omniagent-internal",
        "--namespace",
        config.namespace,
    )
    warm_pool = _kubectl_json(
        "get",
        "sandboxwarmpool",
        config.template,
        "--namespace",
        config.sandbox_namespace,
    )
    _validate_template_and_service(config, template, service)
    _validate_warm_pool(config, warm_pool)
    print(f"READ_ONLY_ACCEPTANCE_OK context={context}")


def _claim_manifest(config: Config, name: str) -> str:
    ttl_seconds = min(
        MAX_LIVE_CLAIM_TTL_SECONDS,
        max(300, config.timeout * 3 + 120),
    )
    shutdown = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=ttl_seconds)
    value = {
        "apiVersion": f"{CLAIM_GROUP}/{CLAIM_VERSION}",
        "kind": "SandboxClaim",
        "metadata": {
            "name": name,
            "namespace": config.sandbox_namespace,
            "labels": {"agent-eval.aidong.ai/staging-smoke": "true"},
        },
        "spec": {
            "sandboxTemplateRef": {"name": config.template},
            "warmpool": "none",
            "lifecycle": {
                "shutdownTime": shutdown.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "shutdownPolicy": "DeleteForeground",
            },
        },
    }
    return json.dumps(value, separators=(",", ":"))


def _pod_name(sandbox: dict[str, Any]) -> str:
    annotations = (sandbox.get("metadata") or {}).get("annotations") or {}
    name = annotations.get("agents.x-k8s.io/pod-name")
    if not isinstance(name, str) or not name:
        _fail("ready Sandbox does not expose its Pod name annotation")
    return name


def _validate_live_pod(config: Config, pod: dict[str, Any]) -> None:
    spec = pod.get("spec") or {}
    security = spec.get("securityContext") or {}
    if spec.get("automountServiceAccountToken") is not False:
        _fail("live sandbox Pod mounts a ServiceAccount token")
    if security.get("runAsNonRoot") is not True:
        _fail("live sandbox Pod is not configured as non-root")
    if security.get("runAsUser") != 10001 or security.get("runAsGroup") != 10001:
        _fail("live sandbox Pod must use UID and GID 10001")
    if (security.get("seccompProfile") or {}).get("type") != "RuntimeDefault":
        _fail("live sandbox Pod must use RuntimeDefault seccomp")

    containers = spec.get("containers") or []
    if len(containers) != 1 or containers[0].get("name") != "runtime":
        _fail("live sandbox Pod must contain only the runtime container")
    container = containers[0]
    if container.get("image") != config.analysis_image:
        _fail("live sandbox Pod image does not match the reviewed digest")
    container_security = container.get("securityContext") or {}
    if container_security.get("allowPrivilegeEscalation") is not False:
        _fail("live sandbox runtime permits privilege escalation")
    if container_security.get("readOnlyRootFilesystem") is not True:
        _fail("live sandbox runtime root filesystem is not read-only")
    if (container_security.get("capabilities") or {}).get("drop") != ["ALL"]:
        _fail("live sandbox runtime must drop all Linux capabilities")


def _exec_python(namespace: str, pod: str, source: str, *, check: bool = True) -> str:
    result = _run(
        [
            "kubectl",
            "exec",
            "--namespace",
            namespace,
            pod,
            "--",
            "/opt/runtime/bin/python",
            "-c",
            source,
        ],
        check=check,
    )
    return result.stdout.strip()


def _cleanup_claims(names: list[str], namespace: str, timeout: int) -> None:
    failures: list[str] = []
    for name in names:
        result = _run(
            [
                "kubectl",
                "delete",
                "sandboxclaim",
                name,
                "--namespace",
                namespace,
                "--ignore-not-found=true",
                "--wait=true",
                f"--timeout={timeout}s",
            ],
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            failures.append(f"{name}: delete failed: {detail}")
            continue
        try:
            _wait_absent("sandboxclaim", name, namespace, timeout)
        except SmokeFailure as exc:
            failures.append(f"{name}: {exc}")
    if failures:
        _fail("staging smoke claim cleanup failed: " + "; ".join(failures))


def _live_acceptance(config: Config) -> None:
    if config.confirmation != LIVE_CONFIRMATION:
        _fail(f"live acceptance requires --confirm-live {LIVE_CONFIRMATION}")
    subject = f"system:serviceaccount:{config.namespace}:{config.service_account}"
    suffix = f"{int(time.time())}-{os.getpid()}"
    names = [f"ae-smoke-a-{suffix}", f"ae-smoke-b-{suffix}"]
    created: list[str] = []
    primary_failure: BaseException | None = None
    try:
        sandbox_names: list[str] = []
        sandboxes: list[dict[str, Any]] = []
        for name in names:
            _kubectl(
                "create",
                "--as",
                subject,
                "--namespace",
                config.sandbox_namespace,
                "-f",
                "-",
                input_text=_claim_manifest(config, name),
            )
            created.append(name)
            claim = _wait_json(
                "sandboxclaim",
                name,
                config.sandbox_namespace,
                lambda item: bool((item.get("status") or {}).get("sandbox", {}).get("name")),
                config.timeout,
            )
            lifecycle = (claim.get("spec") or {}).get("lifecycle") or {}
            if lifecycle.get("shutdownPolicy") != "DeleteForeground":
                _fail("smoke claim did not retain its foreground-delete lifecycle")
            sandbox_name = (claim.get("status") or {}).get("sandbox", {}).get("name")
            if not isinstance(sandbox_name, str) or not DNS_LABEL.fullmatch(sandbox_name):
                _fail("smoke claim returned an invalid Sandbox name")
            sandbox = _wait_json(
                "sandbox",
                sandbox_name,
                config.sandbox_namespace,
                _condition_ready,
                config.timeout,
            )
            sandbox_names.append(sandbox_name)
            sandboxes.append(sandbox)

        pods = [_pod_name(item) for item in sandboxes]
        for pod in pods:
            pod_doc = _kubectl_json(
                "get", "pod", pod, "--namespace", config.sandbox_namespace
            )
            _validate_live_pod(config, pod_doc)
            output = _exec_python(
                config.sandbox_namespace,
                pod,
                "import os; assert not os.path.exists('/var/run/secrets/kubernetes.io/serviceaccount/token'); print(os.getuid())",
            )
            if output != "10001":
                _fail(f"live sandbox runtime UID is not 10001: {output}")

        _exec_python(
            config.sandbox_namespace,
            pods[0],
            "import http.client; "
            f"c=http.client.HTTPConnection('agent-eval-omniagent-internal.{config.namespace}.svc',8000,timeout=3); "
            "c.request('GET','/health'); r=c.getresponse(); body=r.read(); "
            "assert r.status == 200, (r.status, body); print('internal-health-ok')",
        )
        internet_result = _run(
            [
                "kubectl",
                "exec",
                "--namespace",
                config.sandbox_namespace,
                pods[0],
                "--",
                "/opt/runtime/bin/python",
                "-c",
                "import socket; socket.create_connection(('1.1.1.1',443),3)",
            ],
            check=False,
        )
        if internet_result.returncode == 0:
            _fail("sandbox can reach the public Internet")

        service_fqdn = (sandboxes[1].get("status") or {}).get("serviceFQDN")
        if not isinstance(service_fqdn, str) or not service_fqdn:
            _fail("second Sandbox does not expose a service FQDN")
        peer_result = _run(
            [
                "kubectl",
                "exec",
                "--namespace",
                config.sandbox_namespace,
                pods[0],
                "--",
                "/opt/runtime/bin/python",
                "-c",
                f"import socket; socket.create_connection(({service_fqdn!r},8888),3)",
            ],
            check=False,
        )
        if peer_result.returncode == 0:
            _fail("one sandbox can connect directly to another sandbox")

        expiry = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
            seconds=TTL_EXPIRY_SECONDS
        )
        lifecycle_patch = json.dumps(
            {
                "spec": {
                    "lifecycle": {
                        "shutdownTime": expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "shutdownPolicy": "DeleteForeground",
                    }
                }
            },
            separators=(",", ":"),
        )
        _kubectl(
            "patch",
            "sandboxclaim",
            names[0],
            "--namespace",
            config.sandbox_namespace,
            "--type=merge",
            "--patch",
            lifecycle_patch,
        )
        deletion_timeout = max(60, min(config.timeout + 30, 300))
        _wait_absent(
            "sandboxclaim", names[0], config.sandbox_namespace, deletion_timeout
        )
        _wait_absent(
            "sandbox", sandbox_names[0], config.sandbox_namespace, deletion_timeout
        )
        created.remove(names[0])
        print("LIVE_CLAIM_ACCEPTANCE_OK")
    except BaseException as exc:
        primary_failure = exc
        raise
    finally:
        try:
            _cleanup_claims(created, config.sandbox_namespace, config.timeout)
        except SmokeFailure as cleanup_failure:
            if primary_failure is not None:
                raise SmokeFailure(
                    f"{primary_failure}; additionally, {cleanup_failure}"
                ) from primary_failure
            raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--namespace", default="agent-eval")
    parser.add_argument("--sandbox-namespace", default="omniagent-sandbox-staging")
    parser.add_argument("--deployment", default="backend")
    parser.add_argument("--template", default="omniagent-execution-v1")
    parser.add_argument("--service-account", default="omniagent-executor")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--analysis-image", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-live", default="")
    return parser


def main() -> int:
    args = _parser().parse_args()
    config = Config(
        namespace=args.namespace,
        sandbox_namespace=args.sandbox_namespace,
        deployment=args.deployment,
        template=args.template,
        service_account=args.service_account,
        tenant_id=args.tenant_id,
        analysis_image=args.analysis_image,
        timeout=max(30, min(args.timeout, 600)),
        live=args.live,
        confirmation=args.confirm_live,
    )
    try:
        _validate_config(config)
        _read_only_acceptance(config)
        if config.live:
            _live_acceptance(config)
    except (SmokeFailure, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"STAGING_ACCEPTANCE_FAILED: {exc}", file=sys.stderr)
        return 1
    print("STAGING_ACCEPTANCE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
