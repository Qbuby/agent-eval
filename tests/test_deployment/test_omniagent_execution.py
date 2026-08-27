from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
EXECUTION = ROOT / "deploy" / "k8s" / "omniagent-execution"
TENANT_ID = "11111111-1111-4111-8111-111111111111"
ORIGINAL_SERVICE_ACCOUNT = "backend-runtime"
EXECUTION_ENV_NAMES = {
    "OMNIAGENT_EXECUTION_ENABLED",
    "OMNIAGENT_EXECUTION_TENANT_ALLOWLIST",
    "OMNIAGENT_EXECUTION_SECRET_KEY",
    "OMNIAGENT_WORKER_ENABLED",
    "OMNIAGENT_RUNNER",
    "OMNIAGENT_KUBERNETES_RUNNER_CONFIRMED",
    "OMNIAGENT_KUBERNETES_NAMESPACE",
    "OMNIAGENT_KUBERNETES_TEMPLATE",
    "OMNIAGENT_KUBERNETES_READY_TIMEOUT_SECONDS",
    "OMNIAGENT_KUBERNETES_CLAIM_TTL_SECONDS",
    "OMNIAGENT_ARTIFACT_SCANNER",
    "OMNIAGENT_CLAMAV_COMMAND",
}


def _documents(name: str, *, image: str | None = None) -> list[dict]:
    text = (EXECUTION / name).read_text(encoding="utf-8")
    text = text.replace("${NAMESPACE}", "agent-eval")
    if image is not None:
        text = text.replace("${ANALYSIS_RUNTIME_IMAGE}", image)
    return [
        document
        for document in yaml.safe_load_all(text)
        if document
    ]


def _render_patch(name: str) -> str:
    return (
        (EXECUTION / name).read_text(encoding="utf-8")
        .replace("${DEPLOYMENT_NAME}", "backend")
        .replace("${NAMESPACE}", "agent-eval")
        .replace("${EXECUTION_TENANT_ALLOWLIST}", TENANT_ID)
        .replace("${EXECUTION_SECRET_NAME}", "omniagent-execution-secret")
        .replace("${ORIGINAL_SERVICE_ACCOUNT}", ORIGINAL_SERVICE_ACCOUNT)
    )


def _local_patch(deployment: str, patch: str) -> str:
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        pytest.skip("kubectl is unavailable")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        deployment_file = temp / "deployment.yaml"
        patch_file = temp / "patch.yaml"
        deployment_file.write_text(deployment, encoding="utf-8")
        patch_file.write_text(patch, encoding="utf-8")
        return subprocess.run(
            [
                kubectl,
                "patch",
                "--local",
                "--type=strategic",
                "--filename",
                str(deployment_file),
                "--patch-file",
                str(patch_file),
                "--output=yaml",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout


def test_apply_reads_annotation_keys_with_go_template_index() -> None:
    script = (EXECUTION / "apply.sh").read_text(encoding="utf-8")
    annotation_keys = (
        "agent-eval.aidong.ai/omniagent-previous-service-account",
        "agent-eval.aidong.ai/omniagent-restored-service-account",
        "agent-eval.aidong.ai/omniagent-execution",
    )

    for key in annotation_keys:
        expected = (
            'go-template={{with index .spec.template.metadata.annotations "'
            + key
            + '"}}{{.}}{{end}}'
        )
        assert expected in script
        assert f"annotations['{key}']" not in script


def test_execution_template_is_fail_closed_and_hardened() -> None:
    documents = _documents(
        "20-sandbox-template.yaml.tpl",
        image="registry.invalid/agent-eval/analysis-runtime@sha256:" + "0" * 64,
    )
    template = next(item for item in documents if item["kind"] == "SandboxTemplate")
    warm_pool = next(item for item in documents if item["kind"] == "SandboxWarmPool")

    assert template["apiVersion"] == "extensions.agents.x-k8s.io/v1alpha1"
    assert template["spec"]["envVarsInjectionPolicy"] == "Disallowed"
    assert template["spec"]["service"] is True
    pod = template["spec"]["podTemplate"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    container = pod["containers"][0]
    assert container["image"].endswith("@sha256:" + "0" * 64)
    assert "analysis-runtime" in container["image"]
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert warm_pool["spec"]["replicas"] == 0


def test_execution_network_has_only_router_dns_and_internal_api() -> None:
    template = _documents(
        "20-sandbox-template.yaml.tpl",
        image="registry.invalid/agent-eval/analysis-runtime@sha256:" + "0" * 64,
    )[0]
    policy = template["spec"]["networkPolicy"]
    ingress = policy["ingress"]
    ingress_sources = {
        (
            target["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"],
            tuple(sorted(target["podSelector"]["matchLabels"].items())),
        )
        for target in ingress[0]["from"]
    }
    assert ingress_sources == {
        ("omniagent-sandbox-staging", (("app", "sandbox-router"),)),
        ("agent-eval", (("app", "backend"),)),
    }
    ports = {
        (port["protocol"], port["port"])
        for rule in policy["egress"]
        for port in rule.get("ports", [])
    }
    assert ports == {("UDP", 53), ("TCP", 53), ("TCP", 8000)}
    assert not any("ipBlock" in target for rule in policy["egress"] for target in rule["to"])


def test_execution_rbac_cannot_create_pods_or_secrets() -> None:
    documents = _documents("00-namespace-rbac.yaml")
    role = next(item for item in documents if item["kind"] == "Role")
    permissions = {
        (rule["apiGroups"][0], resource, frozenset(rule["verbs"]))
        for rule in role["rules"]
        for resource in rule["resources"]
    }
    assert permissions == {
        (
            "extensions.agents.x-k8s.io",
            "sandboxclaims",
            frozenset({"get", "list", "watch", "create", "delete"}),
        ),
        (
            "extensions.agents.x-k8s.io",
            "sandboxtemplates",
            frozenset({"get", "list", "watch"}),
        ),
        (
            "agents.x-k8s.io",
            "sandboxes",
            frozenset({"get", "list", "watch"}),
        ),
    }
    resources = {item[1] for item in permissions}
    assert "pods" not in resources
    assert "secrets" not in resources


def test_execution_identity_is_an_explicit_backend_patch() -> None:
    patch = yaml.safe_load(
        (EXECUTION / "backend-executor-patch.yaml.tpl").read_text(encoding="utf-8")
        .replace("${DEPLOYMENT_NAME}", "backend")
        .replace("${NAMESPACE}", "agent-eval")
        .replace("${EXECUTION_TENANT_ALLOWLIST}", TENANT_ID)
        .replace("${EXECUTION_SECRET_NAME}", "omniagent-execution-secret")
        .replace("${ORIGINAL_SERVICE_ACCOUNT}", ORIGINAL_SERVICE_ACCOUNT)
    )
    pod = patch["spec"]["template"]["spec"]
    assert pod["serviceAccountName"] == "omniagent-executor"
    backend = next(item for item in pod["containers"] if item["name"] == "backend")
    env = {item["name"]: item for item in backend["env"]}
    assert env["OMNIAGENT_EXECUTION_ENABLED"]["value"] == "true"
    assert env["OMNIAGENT_EXECUTION_TENANT_ALLOWLIST"]["value"] == TENANT_ID
    assert env["OMNIAGENT_EXECUTION_SECRET_KEY"]["valueFrom"]["secretKeyRef"] == {
        "name": "omniagent-execution-secret",
        "key": "OMNIAGENT_EXECUTION_SECRET_KEY",
    }
    assert env["OMNIAGENT_PRODUCT_PLANE_ENABLED"]["value"] == "true"
    assert env["OMNIAGENT_WORKER_ENABLED"]["value"] == "true"
    assert env["OMNIAGENT_RUNNER"]["value"] == "kubernetes"
    assert env["OMNIAGENT_KUBERNETES_RUNNER_CONFIRMED"]["value"] == "true"
    assert env["OMNIAGENT_ARTIFACT_SCANNER"]["value"] == "clamav"
    assert patch["spec"]["template"]["metadata"]["annotations"] == {
        "agent-eval.aidong.ai/omniagent-execution": "enabled",
        "agent-eval.aidong.ai/omniagent-previous-service-account": ORIGINAL_SERVICE_ACCOUNT,
        "agent-eval.aidong.ai/omniagent-restored-service-account": None,
    }


def test_execution_patches_round_trip_locally_and_preserve_product_access() -> None:
    deployment = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: backend
  namespace: agent-eval
spec:
  selector:
    matchLabels:
      app: backend
  template:
    metadata:
      labels:
        app: backend
      annotations:
        example.invalid/retained: yes
    spec:
      serviceAccountName: backend-runtime
      containers:
        - name: backend
          image: backend:local
          env:
            - name: UNRELATED_BACKEND_SETTING
              value: retained
        - name: omniagent
          image: omniagent:local
          env:
            - name: UNRELATED_OMNIAGENT_SETTING
              value: retained
"""
    enabled = _local_patch(
        deployment,
        _render_patch("backend-executor-patch.yaml.tpl"),
    )
    enabled_doc = yaml.safe_load(enabled)
    enabled_template = enabled_doc["spec"]["template"]
    assert enabled_template["spec"]["serviceAccountName"] == "omniagent-executor"
    assert enabled_template["metadata"]["annotations"][
        "agent-eval.aidong.ai/omniagent-previous-service-account"
    ] == ORIGINAL_SERVICE_ACCOUNT

    stopped = _local_patch(
        enabled,
        _render_patch("backend-executor-stop-patch.yaml.tpl"),
    )
    stopped_doc = yaml.safe_load(stopped)
    stopped_backend = next(
        item
        for item in stopped_doc["spec"]["template"]["spec"]["containers"]
        if item["name"] == "backend"
    )
    stopped_env = {item["name"]: item for item in stopped_backend["env"]}
    assert stopped_env["OMNIAGENT_EXECUTION_ENABLED"]["value"] == "false"
    assert stopped_env["OMNIAGENT_WORKER_ENABLED"]["value"] == "false"

    cleaned = _local_patch(
        stopped,
        _render_patch("backend-executor-disable-patch.yaml.tpl"),
    )
    cleaned_doc = yaml.safe_load(cleaned)
    cleaned_template = cleaned_doc["spec"]["template"]
    assert cleaned_template["spec"]["serviceAccountName"] == ORIGINAL_SERVICE_ACCOUNT
    annotations = cleaned_template["metadata"]["annotations"]
    assert "agent-eval.aidong.ai/omniagent-previous-service-account" not in annotations
    assert annotations["agent-eval.aidong.ai/omniagent-execution"] == "disabled"
    assert annotations[
        "agent-eval.aidong.ai/omniagent-restored-service-account"
    ] == ORIGINAL_SERVICE_ACCOUNT
    assert annotations["example.invalid/retained"] is True

    containers = {
        item["name"]: item for item in cleaned_template["spec"]["containers"]
    }
    assert set(containers) == {"backend", "omniagent"}
    backend_env = {item["name"]: item for item in containers["backend"]["env"]}
    assert backend_env["UNRELATED_BACKEND_SETTING"]["value"] == "retained"
    assert backend_env["OMNIAGENT_PRODUCT_PLANE_ENABLED"]["value"] == "true"
    assert EXECUTION_ENV_NAMES.isdisjoint(backend_env)
    omniagent_env = {
        item["name"]: item for item in containers["omniagent"]["env"]
    }
    assert omniagent_env == {
        "UNRELATED_OMNIAGENT_SETTING": {
            "name": "UNRELATED_OMNIAGENT_SETTING",
            "value": "retained",
        }
    }

    reenabled = _local_patch(
        cleaned,
        _render_patch("backend-executor-patch.yaml.tpl"),
    )
    reenabled_doc = yaml.safe_load(reenabled)
    reenabled_template = reenabled_doc["spec"]["template"]
    assert reenabled_template["spec"]["serviceAccountName"] == "omniagent-executor"
    reenabled_annotations = reenabled_template["metadata"]["annotations"]
    assert reenabled_annotations[
        "agent-eval.aidong.ai/omniagent-previous-service-account"
    ] == ORIGINAL_SERVICE_ACCOUNT
    assert (
        "agent-eval.aidong.ai/omniagent-restored-service-account"
        not in reenabled_annotations
    )


def test_execution_apply_script_renders_offline_and_requires_digest() -> None:
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("POSIX shell is unavailable")
    script = EXECUTION / "apply.sh"
    digest_image = "registry.example/analysis-runtime@sha256:" + "a" * 64
    environment = {
        **os.environ,
        "MODE": "render",
        "ANALYSIS_RUNTIME_IMAGE": digest_image,
        "EXECUTION_TENANT_ALLOWLIST": TENANT_ID,
        "ORIGINAL_SERVICE_ACCOUNT": ORIGINAL_SERVICE_ACCOUNT,
    }
    rendered = subprocess.run(
        [shell, str(script)],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    assert digest_image in rendered
    assert "# --- resources (kubectl apply) ---" in rendered
    assert "# --- backend identity (kubectl strategic merge patch) ---" in rendered
    assert "serviceAccountName: omniagent-executor" in rendered
    assert "${" not in rendered

    custom_namespace = subprocess.run(
        [shell, str(script)],
        cwd=ROOT,
        env={
            **environment,
            "NAMESPACE": "agent-eval-review",
            "ENABLE_AXI_TOOLS": "1",
            "CONFIRM_AXI_LICENSE_REVIEWED": "axi-license-reviewed",
            "AXI_RUNTIME_IMAGE": "registry.example/axi@sha256:" + "b" * 64,
        },
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    assert 'namespace: "agent-eval-review"' in custom_namespace
    assert "kubernetes.io/metadata.name: \"agent-eval-review\"" in custom_namespace
    assert "http://agent-eval-omniagent-internal.agent-eval-review.svc:8000" in custom_namespace
    assert "${NAMESPACE}" not in custom_namespace

    rejected = subprocess.run(
        [shell, str(script)],
        cwd=ROOT,
        env={
            **os.environ,
            "MODE": "render",
            "EXECUTION_TENANT_ALLOWLIST": TENANT_ID,
            "ORIGINAL_SERVICE_ACCOUNT": ORIGINAL_SERVICE_ACCOUNT,
            "ANALYSIS_RUNTIME_IMAGE": "registry.example/analysis-runtime:latest",
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert rejected.returncode != 0
    assert "@sha256" in rejected.stderr

    injected = subprocess.run(
        [shell, str(script)],
        cwd=ROOT,
        env={
            **os.environ,
            "MODE": "render",
            "EXECUTION_TENANT_ALLOWLIST": TENANT_ID,
            "ORIGINAL_SERVICE_ACCOUNT": ORIGINAL_SERVICE_ACCOUNT,
            "ANALYSIS_RUNTIME_IMAGE": (
                "registry.example/analysis\nunsafe: value@sha256:" + "a" * 64
            ),
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert injected.returncode != 0
    assert "unsafe for manifest rendering" in injected.stderr

    invalid_name = subprocess.run(
        [shell, str(script)],
        cwd=ROOT,
        env={
            **os.environ,
            "MODE": "render",
            "NAMESPACE": "agent-eval\nother",
            "EXECUTION_TENANT_ALLOWLIST": TENANT_ID,
            "ORIGINAL_SERVICE_ACCOUNT": ORIGINAL_SERVICE_ACCOUNT,
            "ANALYSIS_RUNTIME_IMAGE": digest_image,
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert invalid_name.returncode != 0
    assert "valid lowercase Kubernetes DNS name" in invalid_name.stderr

    executor_restore = subprocess.run(
        [shell, str(script)],
        cwd=ROOT,
        env={
            **os.environ,
            "MODE": "render",
            "EXECUTION_TENANT_ALLOWLIST": TENANT_ID,
            "ORIGINAL_SERVICE_ACCOUNT": "omniagent-executor",
            "ANALYSIS_RUNTIME_IMAGE": digest_image,
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert executor_restore.returncode != 0
    assert "must not be omniagent-executor" in executor_restore.stderr


def test_axi_tools_render_is_separately_license_gated() -> None:
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("POSIX shell is unavailable")
    script = EXECUTION / "apply.sh"
    analysis_image = "registry.example/analysis@sha256:" + "a" * 64
    axi_image = "registry.example/axi@sha256:" + "b" * 64

    analysis_only = subprocess.run(
        [shell, str(script)],
        cwd=ROOT,
        env={
            **os.environ,
            "MODE": "render",
            "EXECUTION_TENANT_ALLOWLIST": TENANT_ID,
            "ORIGINAL_SERVICE_ACCOUNT": ORIGINAL_SERVICE_ACCOUNT,
            "ANALYSIS_RUNTIME_IMAGE": analysis_image,
        },
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    assert "agent-eval-axi-v1" not in analysis_only
    assert "OMNIAGENT_AXI_TOOLS_ENABLED" not in analysis_only

    missing_review = subprocess.run(
        [shell, str(script)],
        cwd=ROOT,
        env={
            **os.environ,
            "MODE": "render",
            "ENABLE_AXI_TOOLS": "1",
            "EXECUTION_TENANT_ALLOWLIST": TENANT_ID,
            "ORIGINAL_SERVICE_ACCOUNT": ORIGINAL_SERVICE_ACCOUNT,
            "ANALYSIS_RUNTIME_IMAGE": analysis_image,
            "AXI_RUNTIME_IMAGE": axi_image,
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert missing_review.returncode != 0
    assert "CONFIRM_AXI_LICENSE_REVIEWED" in missing_review.stderr

    enabled = subprocess.run(
        [shell, str(script)],
        cwd=ROOT,
        env={
            **os.environ,
            "MODE": "render",
            "ENABLE_AXI_TOOLS": "1",
            "CONFIRM_AXI_LICENSE_REVIEWED": "axi-license-reviewed",
            "EXECUTION_TENANT_ALLOWLIST": TENANT_ID,
            "ORIGINAL_SERVICE_ACCOUNT": ORIGINAL_SERVICE_ACCOUNT,
            "ANALYSIS_RUNTIME_IMAGE": analysis_image,
            "AXI_RUNTIME_IMAGE": axi_image,
        },
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    assert axi_image in enabled
    assert "name: agent-eval-axi-v1" in enabled
    assert "name: OMNIAGENT_AXI_TOOLS_ENABLED" in enabled
    assert "name: AGENT_EVAL_INTERNAL_URL" in enabled
    assert "${" not in enabled

    rollback = subprocess.run(
        [shell, str(script)],
        cwd=ROOT,
        env={
            **os.environ,
            "MODE": "render",
            "DISABLE_AXI_TOOLS": "1",
        },
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    assert "OmniAgent Axi tools rollback" in rollback
    assert "$patch: delete" in rollback
    assert "name: OMNIAGENT_AXI_TOOLS_ENABLED" in rollback
    assert "agent-eval-axi-v1" not in rollback
    assert "# --- resources (kubectl apply) ---" not in rollback

    conflicting = subprocess.run(
        [shell, str(script)],
        cwd=ROOT,
        env={
            **os.environ,
            "MODE": "render",
            "ENABLE_AXI_TOOLS": "1",
            "DISABLE_AXI_TOOLS": "1",
            "CONFIRM_AXI_LICENSE_REVIEWED": "axi-license-reviewed",
            "EXECUTION_TENANT_ALLOWLIST": TENANT_ID,
            "ORIGINAL_SERVICE_ACCOUNT": ORIGINAL_SERVICE_ACCOUNT,
            "ANALYSIS_RUNTIME_IMAGE": analysis_image,
            "AXI_RUNTIME_IMAGE": axi_image,
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert conflicting.returncode != 0
    assert "cannot both" in conflicting.stderr


def test_execution_apply_preflight_fails_before_any_mutation() -> None:
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("POSIX shell is unavailable")
    script = EXECUTION / "apply.sh"
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        log = temp / "kubectl.log"
        fake = temp / "kubectl"
        fake.write_text(
            "#!/usr/bin/env sh\n"
            "printf '%s\\n' \"$*\" >> \"$FAKE_KUBECTL_LOG\"\n"
            "case \"$*\" in\n"
            "  *'jsonpath={.spec.template.metadata.labels.app}'*) printf wrong ;;\n"
            "  *'jsonpath={range .spec.template.spec.containers[*]}'*) printf 'backend\\nomniagent\\n' ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        result = subprocess.run(
            [shell, str(script)],
            cwd=ROOT,
            env={
                **os.environ,
                "PATH": f"{temp}{os.pathsep}{os.environ.get('PATH', '')}",
                "FAKE_KUBECTL_LOG": str(log),
                "MODE": "apply",
                "CONFIRM_EXECUTION_APPLY": "apply-omniagent-execution",
                "EXECUTION_TENANT_ALLOWLIST": TENANT_ID,
                "ORIGINAL_SERVICE_ACCOUNT": ORIGINAL_SERVICE_ACCOUNT,
                "ANALYSIS_RUNTIME_IMAGE": "registry.example/analysis@sha256:" + "a" * 64,
            },
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        calls = log.read_text(encoding="utf-8")

    assert result.returncode != 0
    assert "app=backend" in result.stderr
    assert " apply " not in f" {calls} "
    assert " patch " not in f" {calls} "
    assert calls.count("get deployment") == 1


def test_execution_secret_key_preflight_fails_before_any_mutation() -> None:
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("POSIX shell is unavailable")
    script = EXECUTION / "apply.sh"
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        log = temp / "kubectl.log"
        fake = temp / "kubectl"
        fake.write_text(
            "#!/usr/bin/env sh\n"
            "printf '%s\\n' \"$*\" >> \"$FAKE_KUBECTL_LOG\"\n"
            "case \"$*\" in\n"
            "  *'jsonpath={.spec.template.metadata.labels.app}'*) printf backend ;;\n"
            "  *'jsonpath={range .spec.template.spec.containers[*]}'*) printf 'backend\\nomniagent\\n' ;;\n"
            "  *'jsonpath={.data.OMNIAGENT_EXECUTION_SECRET_KEY}'*) printf '' ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        result = subprocess.run(
            [shell, str(script)],
            cwd=ROOT,
            env={
                **os.environ,
                "PATH": f"{temp}{os.pathsep}{os.environ.get('PATH', '')}",
                "FAKE_KUBECTL_LOG": str(log),
                "MODE": "apply",
                "CONFIRM_EXECUTION_APPLY": "apply-omniagent-execution",
                "EXECUTION_TENANT_ALLOWLIST": TENANT_ID,
                "ORIGINAL_SERVICE_ACCOUNT": ORIGINAL_SERVICE_ACCOUNT,
                "ANALYSIS_RUNTIME_IMAGE": "registry.example/analysis@sha256:" + "a" * 64,
            },
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        calls = log.read_text(encoding="utf-8")

    assert result.returncode != 0
    assert "missing data.OMNIAGENT_EXECUTION_SECRET_KEY" in result.stderr
    assert "get secret omniagent-execution-secret" in calls
    assert " apply " not in f" {calls} "
    assert " patch " not in f" {calls} "


def test_execution_rollback_fails_before_mutation_without_previous_identity() -> None:
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("POSIX shell is unavailable")
    script = EXECUTION / "apply.sh"
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        log = temp / "kubectl.log"
        fake = temp / "kubectl"
        fake.write_text(
            "#!/usr/bin/env sh\n"
            "printf '%s\\n' \"$*\" >> \"$FAKE_KUBECTL_LOG\"\n"
            "case \"$*\" in\n"
            "  *'jsonpath={.spec.template.metadata.labels.app}'*) printf backend ;;\n"
            "  *'jsonpath={range .spec.template.spec.containers[*]}'*) printf 'backend\\nomniagent\\n' ;;\n"
            "  *'jsonpath={.spec.template.spec.serviceAccountName}'*) printf omniagent-executor ;;\n"
            "  *omniagent-previous-service-account*) printf '' ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        result = subprocess.run(
            [shell, str(script)],
            cwd=ROOT,
            env={
                **os.environ,
                "PATH": f"{temp}{os.pathsep}{os.environ.get('PATH', '')}",
                "FAKE_KUBECTL_LOG": str(log),
                "MODE": "apply",
                "DISABLE_EXECUTION": "1",
                "CONFIRM_EXECUTION_APPLY": "apply-omniagent-execution",
            },
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        calls = log.read_text(encoding="utf-8")

    assert result.returncode != 0
    assert "previous identity annotation is missing" in result.stderr
    assert " apply " not in f" {calls} "
    assert " patch " not in f" {calls} "
    assert " rollout " not in f" {calls} "


def test_execution_rollback_rejects_executor_as_recorded_identity() -> None:
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("POSIX shell is unavailable")
    script = EXECUTION / "apply.sh"
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        log = temp / "kubectl.log"
        fake = temp / "kubectl"
        fake.write_text(
            "#!/usr/bin/env sh\n"
            "printf '%s\\n' \"$*\" >> \"$FAKE_KUBECTL_LOG\"\n"
            "case \"$*\" in\n"
            "  *'jsonpath={.spec.template.metadata.labels.app}'*) printf backend ;;\n"
            "  *'jsonpath={range .spec.template.spec.containers[*]}'*) printf 'backend\\nomniagent\\n' ;;\n"
            "  *'jsonpath={.spec.template.spec.serviceAccountName}'*) printf omniagent-executor ;;\n"
            "  *omniagent-previous-service-account*) printf omniagent-executor ;;\n"
            "  *omniagent-restored-service-account*) printf '' ;;\n"
            "  *omniagent-execution*) printf enabled ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        result = subprocess.run(
            [shell, str(script)],
            cwd=ROOT,
            env={
                **os.environ,
                "PATH": f"{temp}{os.pathsep}{os.environ.get('PATH', '')}",
                "FAKE_KUBECTL_LOG": str(log),
                "MODE": "apply",
                "DISABLE_EXECUTION": "1",
                "CONFIRM_EXECUTION_APPLY": "apply-omniagent-execution",
            },
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        calls = log.read_text(encoding="utf-8")

    assert result.returncode != 0
    assert "must not be omniagent-executor" in result.stderr
    assert " apply " not in f" {calls} "
    assert " patch " not in f" {calls} "
    assert " rollout " not in f" {calls} "


def test_execution_rollback_applies_stop_axi_cleanup_and_identity_in_order() -> None:
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("POSIX shell is unavailable")
    script = EXECUTION / "apply.sh"
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        log = temp / "kubectl.log"
        fake = temp / "kubectl"
        fake.write_text(
            "#!/usr/bin/env sh\n"
            "printf '%s\\n' \"$*\" >> \"$FAKE_KUBECTL_LOG\"\n"
            "case \"$*\" in\n"
            "  *'jsonpath={.spec.template.metadata.labels.app}'*) printf backend ;;\n"
            "  *'jsonpath={range .spec.template.spec.containers[*]}'*) printf 'backend\\nomniagent\\n' ;;\n"
            "  *'jsonpath={.spec.template.spec.serviceAccountName}'*) printf omniagent-executor ;;\n"
            "  *omniagent-previous-service-account*) printf backend-runtime ;;\n"
            "  *omniagent-restored-service-account*) printf '' ;;\n"
            "  *omniagent-execution*) printf enabled ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        subprocess.run(
            [shell, str(script)],
            cwd=ROOT,
            env={
                **os.environ,
                "PATH": f"{temp}{os.pathsep}{os.environ.get('PATH', '')}",
                "FAKE_KUBECTL_LOG": str(log),
                "MODE": "apply",
                "DISABLE_EXECUTION": "1",
                "CONFIRM_EXECUTION_APPLY": "apply-omniagent-execution",
            },
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        calls = log.read_text(encoding="utf-8").splitlines()

    mutations = [
        call for call in calls if call.startswith(("patch ", "rollout "))
    ]
    assert len(mutations) == 5
    assert "backend-executor-stop-patch.yaml" in mutations[0]
    assert mutations[1].startswith("rollout status ")
    assert "axi-tools-disable-patch.yaml" in mutations[2]
    assert "backend-executor-disable-patch.yaml" in mutations[3]
    assert mutations[4].startswith("rollout status ")


def test_execution_enable_rejects_inconsistent_identity_before_mutation() -> None:
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("POSIX shell is unavailable")
    script = EXECUTION / "apply.sh"
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        log = temp / "kubectl.log"
        fake = temp / "kubectl"
        fake.write_text(
            "#!/usr/bin/env sh\n"
            "printf '%s\\n' \"$*\" >> \"$FAKE_KUBECTL_LOG\"\n"
            "case \"$*\" in\n"
            "  *'jsonpath={.spec.template.metadata.labels.app}'*) printf backend ;;\n"
            "  *'jsonpath={range .spec.template.spec.containers[*]}'*) printf 'backend\\nomniagent\\n' ;;\n"
            "  *'jsonpath={.data.OMNIAGENT_EXECUTION_SECRET_KEY}'*) printf c2VjcmV0 ;;\n"
            "  *'jsonpath={.spec.template.spec.serviceAccountName}'*) printf backend-runtime ;;\n"
            "  *omniagent-previous-service-account*) printf '' ;;\n"
            "  *omniagent-restored-service-account*) printf '' ;;\n"
            "  *omniagent-execution*) printf enabled ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        result = subprocess.run(
            [shell, str(script)],
            cwd=ROOT,
            env={
                **os.environ,
                "PATH": f"{temp}{os.pathsep}{os.environ.get('PATH', '')}",
                "FAKE_KUBECTL_LOG": str(log),
                "MODE": "apply",
                "CONFIRM_EXECUTION_APPLY": "apply-omniagent-execution",
                "EXECUTION_TENANT_ALLOWLIST": TENANT_ID,
                "ANALYSIS_RUNTIME_IMAGE": (
                    "registry.example/analysis@sha256:" + "a" * 64
                ),
            },
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        calls = log.read_text(encoding="utf-8")

    assert result.returncode != 0
    assert "initial or completed cleanup state" in result.stderr
    assert " apply " not in f" {calls} "
    assert " patch " not in f" {calls} "
    assert " rollout " not in f" {calls} "


def test_execution_rollback_resumes_final_rollout_after_cleanup_completed() -> None:
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("POSIX shell is unavailable")
    script = EXECUTION / "apply.sh"
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        log = temp / "kubectl.log"
        fake = temp / "kubectl"
        fake.write_text(
            "#!/usr/bin/env sh\n"
            "printf '%s\\n' \"$*\" >> \"$FAKE_KUBECTL_LOG\"\n"
            "case \"$*\" in\n"
            "  *'jsonpath={.spec.template.metadata.labels.app}'*) printf backend ;;\n"
            "  *'jsonpath={range .spec.template.spec.containers[*]}'*) printf 'backend\\nomniagent\\n' ;;\n"
            "  *'jsonpath={.spec.template.spec.serviceAccountName}'*) printf backend-runtime ;;\n"
            "  *omniagent-previous-service-account*) printf '' ;;\n"
            "  *omniagent-restored-service-account*) printf backend-runtime ;;\n"
            "  *omniagent-execution*) printf disabled ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        result = subprocess.run(
            [shell, str(script)],
            cwd=ROOT,
            env={
                **os.environ,
                "PATH": f"{temp}{os.pathsep}{os.environ.get('PATH', '')}",
                "FAKE_KUBECTL_LOG": str(log),
                "MODE": "apply",
                "DISABLE_EXECUTION": "1",
                "CONFIRM_EXECUTION_APPLY": "apply-omniagent-execution",
            },
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        calls = log.read_text(encoding="utf-8").splitlines()

    mutations = [
        call for call in calls if call.startswith(("apply ", "patch ", "rollout "))
    ]
    assert len(mutations) == 1
    assert mutations[0].startswith("rollout status ")
    assert "OmniAgent execution resources applied" in result.stdout


def test_execution_rollback_rejects_unproven_completed_identity() -> None:
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("POSIX shell is unavailable")
    script = EXECUTION / "apply.sh"
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        log = temp / "kubectl.log"
        fake = temp / "kubectl"
        fake.write_text(
            "#!/usr/bin/env sh\n"
            "printf '%s\\n' \"$*\" >> \"$FAKE_KUBECTL_LOG\"\n"
            "case \"$*\" in\n"
            "  *'jsonpath={.spec.template.metadata.labels.app}'*) printf backend ;;\n"
            "  *'jsonpath={range .spec.template.spec.containers[*]}'*) printf 'backend\\nomniagent\\n' ;;\n"
            "  *'jsonpath={.spec.template.spec.serviceAccountName}'*) printf tampered-runtime ;;\n"
            "  *omniagent-previous-service-account*) printf '' ;;\n"
            "  *omniagent-restored-service-account*) printf backend-runtime ;;\n"
            "  *omniagent-execution*) printf disabled ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        result = subprocess.run(
            [shell, str(script)],
            cwd=ROOT,
            env={
                **os.environ,
                "PATH": f"{temp}{os.pathsep}{os.environ.get('PATH', '')}",
                "FAKE_KUBECTL_LOG": str(log),
                "MODE": "apply",
                "DISABLE_EXECUTION": "1",
                "CONFIRM_EXECUTION_APPLY": "apply-omniagent-execution",
            },
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        calls = log.read_text(encoding="utf-8")

    assert result.returncode != 0
    assert "state is inconsistent" in result.stderr
    assert " apply " not in f" {calls} "
    assert " patch " not in f" {calls} "
    assert " rollout " not in f" {calls} "


def test_execution_rollback_rejects_executor_as_restored_identity() -> None:
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("POSIX shell is unavailable")
    script = EXECUTION / "apply.sh"
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        log = temp / "kubectl.log"
        fake = temp / "kubectl"
        fake.write_text(
            "#!/usr/bin/env sh\n"
            "printf '%s\\n' \"$*\" >> \"$FAKE_KUBECTL_LOG\"\n"
            "case \"$*\" in\n"
            "  *'jsonpath={.spec.template.metadata.labels.app}'*) printf backend ;;\n"
            "  *'jsonpath={range .spec.template.spec.containers[*]}'*) printf 'backend\\nomniagent\\n' ;;\n"
            "  *'jsonpath={.spec.template.spec.serviceAccountName}'*) printf omniagent-executor ;;\n"
            "  *omniagent-previous-service-account*) printf '' ;;\n"
            "  *omniagent-restored-service-account*) printf omniagent-executor ;;\n"
            "  *omniagent-execution*) printf disabled ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        result = subprocess.run(
            [shell, str(script)],
            cwd=ROOT,
            env={
                **os.environ,
                "PATH": f"{temp}{os.pathsep}{os.environ.get('PATH', '')}",
                "FAKE_KUBECTL_LOG": str(log),
                "MODE": "apply",
                "DISABLE_EXECUTION": "1",
                "CONFIRM_EXECUTION_APPLY": "apply-omniagent-execution",
            },
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        calls = log.read_text(encoding="utf-8")

    assert result.returncode != 0
    assert "must not be omniagent-executor" in result.stderr
    assert " apply " not in f" {calls} "
    assert " patch " not in f" {calls} "
    assert " rollout " not in f" {calls} "


def test_axi_rollback_apply_needs_no_images_or_resource_apply() -> None:
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("POSIX shell is unavailable")
    script = EXECUTION / "apply.sh"
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        log = temp / "kubectl.log"
        fake = temp / "kubectl"
        fake.write_text(
            "#!/usr/bin/env sh\n"
            "printf '%s\\n' \"$*\" >> \"$FAKE_KUBECTL_LOG\"\n"
            "case \"$*\" in\n"
            "  *'jsonpath={.spec.template.metadata.labels.app}'*) printf backend ;;\n"
            "  *'jsonpath={range .spec.template.spec.containers[*]}'*) printf 'backend\\nomniagent\\n' ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        result = subprocess.run(
            [shell, str(script)],
            cwd=ROOT,
            env={
                **os.environ,
                "PATH": f"{temp}{os.pathsep}{os.environ.get('PATH', '')}",
                "FAKE_KUBECTL_LOG": str(log),
                "MODE": "apply",
                "DISABLE_AXI_TOOLS": "1",
                "CONFIRM_EXECUTION_APPLY": "apply-omniagent-execution",
            },
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        calls = log.read_text(encoding="utf-8")

    assert "apply -f" not in calls
    assert calls.count("patch deployment") == 1
    assert "axi-tools-disable-patch" in calls
    assert "rollout status" in calls
    assert "OmniAgent execution resources applied" in result.stdout


def test_runtime_dockerfile_remains_license_gated() -> None:
    dockerfile = (
        ROOT / "deploy" / "omniagent" / "execution-runtime" / "Dockerfile"
    ).read_text(encoding="utf-8")
    assert (
        "ARG AXI_WHEEL=deploy/omniagent/execution-runtime/vendor/"
        "axi_cli-0.0.11-py3-none-any.whl"
    ) in dockerfile
    assert "ARG CONFIRM_AXI_LICENSE_REVIEWED=" in dockerfile
    assert '[ "$CONFIRM_AXI_LICENSE_REVIEWED" != "axi-license-reviewed" ]' in dockerfile
    assert "Axi image build requires written license review confirmation" in dockerfile
    wheel_path = "/tmp/axi_cli-0.0.11-py3-none-any.whl"
    assert f"verify_axi_wheel.py {wheel_path}" in dockerfile
    assert f"pip install --no-cache-dir \\\n        {wheel_path}" in dockerfile
    assert "axi_cli-0.0.11" in dockerfile
    assert "analysis-runtime/main.py" in dockerfile
    assert "main:app" in dockerfile
    assert "AXI_RICH=0" in dockerfile


def test_analysis_runtime_is_buildable_without_axi() -> None:
    runtime = ROOT / "deploy" / "omniagent" / "analysis-runtime"
    dockerfile = (runtime / "Dockerfile").read_text(encoding="utf-8")
    requirements = (runtime / "requirements.txt").read_text(encoding="utf-8")
    assert "python:3.12.13-slim-bookworm@sha256:" in dockerfile
    assert "4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2" in dockerfile
    assert "main:app" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "axi" not in requirements.lower()
    assert "python-multipart==0.0.32" in requirements


def test_backend_image_includes_governed_data_catalog() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY config ./config" in dockerfile
    assert (ROOT / "config" / "omniagent-data-catalog.yaml").is_file()


def test_backend_kubernetes_runner_dependency_is_opt_in() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG INSTALL_KUBERNETES_RUNNER=0" in dockerfile
    assert "apt-get upgrade -y" in dockerfile
    assert 'if [ "$INSTALL_KUBERNETES_RUNNER" = "1" ]' in dockerfile
    assert "apt-get install -y --no-install-recommends clamav git" in dockerfile
    assert "pip install -e '.[kubernetes-runner]'" in dockerfile
    assert "pip check" in dockerfile
    assert "pip uninstall -y pip setuptools wheel" in dockerfile


def test_backend_metadata_allows_pinned_kubernetes_runner_reference() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.hatch.metadata]" in pyproject
    assert "allow-direct-references = true" in pyproject
