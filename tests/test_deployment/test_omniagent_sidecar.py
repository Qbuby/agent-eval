from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml


os.environ.setdefault("AUTH_SECRET_KEY", "test-secret-key")


REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / "deploy" / "k8s" / "omniagent-sidecar"
PATCH_PATH = DEPLOY_DIR / "backend-patch.yaml.tpl"
SECRET_PATH = DEPLOY_DIR / "secret.example.yaml"
MIGRATION_PATH = (
    REPO_ROOT / "alembic" / "versions" / "0041_add_omniagent_sidecar_endpoint.py"
)
COMPOSE_URL = "http://omniagent:8090/api/agent/langgraph"
SIDECAR_URL = "http://127.0.0.1:8090/api/agent/langgraph"


def _render_patch() -> dict:
    text = PATCH_PATH.read_text(encoding="utf-8")
    replacements = {
        "${DEPLOYMENT_NAME}": "backend",
        "${NAMESPACE}": "agent-eval",
        "${CONFIG_HASH}": "deadbeef",
        "${OMNIAGENT_IMAGE}": "registry.example/omniagent:test",
        "${OMNIAGENT_MODEL}": "gpt-5.6-terra",
        "${OMNIAGENT_BASE_URL}": "https://example.invalid/v1",
        "${SECRET_NAME}": "omniagent-secret",
        "${DB_HOST}": "postgres",
        "${DB_USER}": "postgres",
        "${CONFIGMAP_NAME}": "omniagent-config",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    assert "${" not in text
    return yaml.safe_load(text)


def _kubectl_strategic_merge(base: dict, patch: dict) -> dict:
    """调用真实 kubectl 做离线 strategic merge，避免手写模拟与 K8s 语义漂移。"""
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        import pytest

        pytest.skip("kubectl 未安装，跳过真实 strategic-merge 回归")

    with tempfile.TemporaryDirectory() as tmp:
        base_path = Path(tmp) / "base.yaml"
        patch_path = Path(tmp) / "patch.yaml"
        base_path.write_text(yaml.safe_dump(base, allow_unicode=True), encoding="utf-8")
        patch_path.write_text(yaml.safe_dump(patch, allow_unicode=True), encoding="utf-8")
        result = subprocess.run(
            [
                kubectl,
                "patch",
                "--local",
                "-f",
                str(base_path),
                "--type=strategic",
                "--patch-file",
                str(patch_path),
                "-o",
                "yaml",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    return yaml.safe_load(result.stdout)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("migration_0041", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_patch_adds_sidecar_without_replacing_existing_pod_containers():
    base = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "backend", "namespace": "agent-eval"},
        "spec": {
            "selector": {"matchLabels": {"app": "backend"}},
            "template": {
                "metadata": {"labels": {"app": "backend"}},
                "spec": {
                    "initContainers": [
                        {"name": "wait-for-postgres", "image": "postgres:16-alpine"},
                        {"name": "db-migrate", "image": "registry.example/backend:test"},
                    ],
                    "containers": [
                        {
                            "name": "backend",
                            "image": "registry.example/backend:test",
                            "ports": [{"containerPort": 8000}],
                        }
                    ],
                    "volumes": [{"name": "existing-volume", "emptyDir": {}}],
                },
            },
        },
    }

    merged = _kubectl_strategic_merge(base, _render_patch())
    template = merged["spec"]["template"]
    pod_spec = template["spec"]

    assert "serviceAccountName" not in pod_spec
    assert {item["name"] for item in pod_spec["initContainers"]} == {
        "wait-for-postgres",
        "db-migrate",
    }
    assert {item["name"] for item in pod_spec["containers"]} == {
        "backend",
        "omniagent",
    }
    containers = {item["name"]: item for item in pod_spec["containers"]}
    assert containers["backend"]["image"] == "registry.example/backend:test"
    assert containers["backend"]["ports"] == [{"containerPort": 8000}]
    assert {item["name"] for item in pod_spec["volumes"]} == {
        "existing-volume",
        "omniagent-config",
    }
    config_items = {
        item["path"] for item in next(
            volume for volume in pod_spec["volumes"] if volume["name"] == "omniagent-config"
        )["configMap"]["items"]
    }
    assert "overlay/sitecustomize.py" in config_items
    assert "overlay/omniagent_overlay/axi_tools.py" in config_items
    assert "skills/data-investigation/SKILL.md" in config_items
    assert template["metadata"]["annotations"][
        "kubectl.kubernetes.io/default-container"
    ] == "backend"


def test_sidecar_is_internal_and_uses_secret_refs_only():
    patch = _render_patch()
    sidecar = patch["spec"]["template"]["spec"]["containers"][0]

    assert sidecar["name"] == "omniagent"
    assert sidecar["ports"] == [
        {"name": "omniagent-http", "containerPort": 8090, "protocol": "TCP"}
    ]
    env = {item["name"]: item for item in sidecar["env"]}
    assert env["OPENAI_API_KEY"]["valueFrom"]["secretKeyRef"] == {
        "name": "omniagent-secret",
        "key": "OMNIAGENT_API_KEY",
    }
    assert env["DB_PASSWORD"]["valueFrom"]["secretKeyRef"] == {
        "name": "omniagent-secret",
        "key": "DB_PASSWORD",
    }
    assert "value" not in env["OPENAI_API_KEY"]
    assert "value" not in env["DB_PASSWORD"]

    kinds = []
    for path in DEPLOY_DIR.iterdir():
        if path.suffix in {".yaml", ".tpl"}:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and doc.get("kind"):
                kinds.append(doc["kind"])
    assert "Service" not in kinds
    assert "Ingress" not in kinds


def test_secret_example_contains_placeholders_not_credentials():
    secret = yaml.safe_load(SECRET_PATH.read_text(encoding="utf-8"))
    assert secret["kind"] == "Secret"
    assert secret["stringData"] == {
        "OMNIAGENT_API_KEY": "<set-in-secret-manager>",
        "DB_PASSWORD": "<set-in-secret-manager>",
    }


def test_default_config_has_compose_and_sidecar_presets_without_defaulting_either():
    from agent_eval.config_service import DEFAULT_CONFIGS, ConfigService

    row = next(item for item in DEFAULT_CONFIGS if item["key"] == "target_agent.endpoint_url")
    options, default_index = ConfigService.normalize_options(row["value"])
    values = [item["value"] for item in options]

    assert values.count(COMPOSE_URL) == 1
    assert values.count(SIDECAR_URL) == 1
    assert options[values.index(COMPOSE_URL)]["label"] == "OmniAgent（Docker Compose）"
    assert options[values.index(SIDECAR_URL)]["label"] == "OmniAgent（同 Pod sidecar）"
    assert default_index == 0
    assert options[default_index]["value"] == ""


def test_migration_adds_sidecar_idempotently_and_preserves_default():
    migration = _load_migration_module()
    original = {
        "options": [
            {"value": "http://existing-agent/api", "label": "现有默认"},
            {"value": COMPOSE_URL, "label": "OmniAgent（系统智能体）"},
        ],
        "default_index": 1,
    }

    upgraded = migration._upgrade_value(original)
    upgraded_again = migration._upgrade_value(upgraded)

    assert upgraded == upgraded_again
    assert upgraded["default_index"] == 1
    assert upgraded["options"][1] == {
        "value": COMPOSE_URL,
        "label": "OmniAgent（Docker Compose）",
    }
    assert [item["value"] for item in upgraded["options"]].count(SIDECAR_URL) == 1

    downgraded = migration._downgrade_value(upgraded)
    assert downgraded == original


def test_migration_does_not_duplicate_preexisting_custom_sidecar_url():
    migration = _load_migration_module()
    original = {
        "options": [
            {"value": "http://existing-agent/api", "label": "默认"},
            {"value": SIDECAR_URL, "label": "运维自定义"},
        ],
        "default_index": 0,
    }

    upgraded = migration._upgrade_value(original)
    assert upgraded == original
    assert migration._downgrade_value(upgraded) == original


def test_migration_empty_options_never_defaults_to_sidecar():
    migration = _load_migration_module()

    upgraded = migration._upgrade_value({"options": [], "default_index": 0})

    assert upgraded == {
        "options": [
            {"value": "", "label": None},
            {"value": SIDECAR_URL, "label": "OmniAgent（同 Pod sidecar）"},
        ],
        "default_index": 0,
    }
    assert upgraded["options"][upgraded["default_index"]]["value"] == ""
