from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "k8s" / "omniagent-execution" / "staging_smoke.py"


def _load():
    spec = importlib.util.spec_from_file_location("omniagent_staging_smoke", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _config(module, *, live: bool = False, confirmation: str = ""):
    return module.Config(
        namespace="agent-eval",
        sandbox_namespace="omniagent-sandbox-staging",
        deployment="backend",
        template="omniagent-execution-v1",
        service_account="omniagent-executor",
        tenant_id="11111111-1111-4111-8111-111111111111",
        analysis_image="registry.example/analysis@sha256:" + "a" * 64,
        timeout=30,
        live=live,
        confirmation=confirmation,
    )


def test_staging_smoke_rejects_unsafe_configuration_before_kubectl(monkeypatch) -> None:
    module = _load()
    config = _config(module)
    config = module.Config(**{**config.__dict__, "sandbox_namespace": "bad\nnamespace"})
    monkeypatch.setattr(
        module,
        "_read_only_acceptance",
        lambda _config: pytest.fail("kubectl acceptance must not run"),
    )

    with pytest.raises(module.SmokeFailure, match="DNS name"):
        module._validate_config(config)


@pytest.mark.parametrize(
    ("expected", "returncode", "stdout"),
    (
        (True, 0, "yes\n"),
        (False, 0, "no\n"),
        (False, 1, "no\n"),
    ),
)
def test_expect_can_i_accepts_kubectl_boolean_exit_codes(
    monkeypatch, expected, returncode, stdout
) -> None:
    module = _load()
    monkeypatch.setattr(
        module,
        "_run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=returncode,
            stdout=stdout,
            stderr="",
        ),
    )

    module._expect_can_i(expected, "create", "pods")


def test_expect_can_i_rejects_command_errors(monkeypatch) -> None:
    module = _load()
    monkeypatch.setattr(
        module,
        "_run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="control plane unavailable",
        ),
    )

    with pytest.raises(module.SmokeFailure, match="control plane unavailable"):
        module._expect_can_i(False, "create", "pods")


def test_live_staging_smoke_requires_exact_confirmation_before_claim_creation(
    monkeypatch,
) -> None:
    module = _load()
    config = _config(module, live=True, confirmation="wrong")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        module,
        "_run",
        lambda command, **_kwargs: calls.append(command),
    )

    with pytest.raises(module.SmokeFailure, match="--confirm-live"):
        module._live_acceptance(config)
    assert calls == []


def test_claim_manifest_has_bounded_foreground_delete_lifecycle() -> None:
    module = _load()
    before = dt.datetime.now(dt.timezone.utc)
    body = json.loads(module._claim_manifest(_config(module), "ae-smoke-test"))
    shutdown = dt.datetime.fromisoformat(
        body["spec"]["lifecycle"]["shutdownTime"].replace("Z", "+00:00")
    )

    assert body["metadata"]["namespace"] == "omniagent-sandbox-staging"
    assert body["spec"]["sandboxTemplateRef"] == {"name": "omniagent-execution-v1"}
    assert body["spec"]["warmpool"] == "none"
    assert body["spec"]["lifecycle"]["shutdownPolicy"] == "DeleteForeground"
    assert body["spec"]["lifecycle"]["shutdownTime"].endswith("Z")
    expected_ttl = max(300, _config(module).timeout * 3 + 120)
    assert shutdown >= before + dt.timedelta(seconds=expected_ttl - 1)
    assert "env" not in body["spec"]


def test_live_staging_smoke_always_deletes_created_claims(monkeypatch) -> None:
    module = _load()
    config = _config(
        module,
        live=True,
        confirmation=module.LIVE_CONFIRMATION,
    )
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        if command[:3] == ["kubectl", "apply", "-f"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module, "_run", fake_run)
    monkeypatch.setattr(
        module,
        "_wait_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            module.SmokeFailure("ready timeout")
        ),
    )

    with pytest.raises(module.SmokeFailure, match="ready timeout"):
        module._live_acceptance(config)

    deletes = [command for command in commands if command[:3] == ["kubectl", "delete", "sandboxclaim"]]
    assert len(deletes) == 1
    assert deletes[0][3].startswith("ae-smoke-a-")


def test_read_only_acceptance_checks_server_dry_run_and_rbac(monkeypatch) -> None:
    module = _load()
    config = _config(module)
    can_i: list[tuple[bool, tuple[str, ...]]] = []

    def fake_kubectl(*args, input_text=None):
        del input_text
        if args == ("config", "current-context"):
            return "staging"
        if args == ("api-resources", "-o", "name"):
            return "\n".join(
                {
                    "sandboxclaims.extensions.agents.x-k8s.io",
                    "sandboxtemplates.extensions.agents.x-k8s.io",
                    "sandboxwarmpools.extensions.agents.x-k8s.io",
                    "sandboxes.agents.x-k8s.io",
                }
            )
        raise AssertionError(args)

    template = {
        "spec": {
            "service": True,
            "envVarsInjectionPolicy": "Disallowed",
            "networkPolicyManagement": "Managed",
            "podTemplate": {
                "spec": {
                    "automountServiceAccountToken": False,
                    "securityContext": {"runAsNonRoot": True},
                    "containers": [
                        {
                            "image": config.analysis_image,
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                        }
                    ],
                }
            },
            "networkPolicy": {
                "ingress": [
                    {
                        "from": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {
                                        "kubernetes.io/metadata.name": (
                                            "omniagent-sandbox-staging"
                                        )
                                    }
                                },
                                "podSelector": {
                                    "matchLabels": {"app": "sandbox-router"}
                                },
                            },
                            {
                                "namespaceSelector": {
                                    "matchLabels": {
                                        "kubernetes.io/metadata.name": "agent-eval"
                                    }
                                },
                                "podSelector": {
                                    "matchLabels": {"app": "backend"}
                                },
                            },
                        ],
                        "ports": [{"protocol": "TCP", "port": 8888}],
                    }
                ],
                "egress": [
                    {
                        "to": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {
                                        "kubernetes.io/metadata.name": "kube-system"
                                    }
                                },
                                "podSelector": {
                                    "matchLabels": {"k8s-app": "kube-dns"}
                                },
                            }
                        ],
                        "ports": [
                            {"protocol": "UDP", "port": 53},
                            {"protocol": "TCP", "port": 53},
                        ],
                    },
                    {
                        "to": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {
                                        "kubernetes.io/metadata.name": "agent-eval"
                                    }
                                },
                                "podSelector": {
                                    "matchLabels": {"app": "backend"}
                                },
                            }
                        ],
                        "ports": [{"protocol": "TCP", "port": 8000}],
                    },
                ]
            },
        }
    }
    service = {
        "spec": {
            "selector": {"app": "backend"},
            "ports": [
                {
                    "name": "http",
                    "protocol": "TCP",
                    "port": 8000,
                    "targetPort": 8000,
                }
            ],
        }
    }
    warm_pool = {
        "spec": {
            "replicas": 0,
            "updateStrategy": {"type": "Recreate"},
            "sandboxTemplateRef": {"name": config.template},
        }
    }
    monkeypatch.setattr(module, "_kubectl", fake_kubectl)
    monkeypatch.setattr(
        module,
        "_kubectl_json",
        lambda *args: (
            service
            if args[1] == "service"
            else warm_pool
            if args[1] == "sandboxwarmpool"
            else template
        ),
    )
    monkeypatch.setattr(
        module,
        "_expect_can_i",
        lambda expected, *args: can_i.append((expected, args)),
    )
    monkeypatch.setattr(
        module,
        "_run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="SERVER_DRY_RUN_OK",
            stderr="",
        ),
    )

    module._read_only_acceptance(config)

    assert any(expected for expected, args in can_i if args[:2] == ("create", "sandboxclaims.extensions.agents.x-k8s.io"))
    assert any(not expected for expected, args in can_i if args[:2] == ("create", "pods"))

    bad_template = json.loads(json.dumps(template))
    bad_template["spec"]["networkPolicy"]["egress"][1]["to"][0][
        "podSelector"
    ]["matchLabels"] = {"app": "omniagent"}
    with pytest.raises(module.SmokeFailure, match="egress"):
        module._validate_template_and_service(config, bad_template, service)

    bad_pool = json.loads(json.dumps(warm_pool))
    bad_pool["spec"]["replicas"] = 1
    with pytest.raises(module.SmokeFailure, match="zero replicas"):
        module._validate_warm_pool(config, bad_pool)


def test_wait_absent_rejects_api_errors(monkeypatch) -> None:
    module = _load()
    monkeypatch.setattr(
        module,
        "_run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="control plane unavailable"
        ),
    )

    with pytest.raises(module.SmokeFailure, match="control plane unavailable"):
        module._wait_absent("sandboxclaim", "claim", "namespace", 1)


def test_claim_cleanup_fails_closed_on_delete_error(monkeypatch) -> None:
    module = _load()
    monkeypatch.setattr(
        module,
        "_run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="forbidden"
        ),
    )

    with pytest.raises(module.SmokeFailure, match="cleanup failed.*forbidden"):
        module._cleanup_claims(["claim-a"], "namespace", 30)


def test_live_pod_validation_rejects_runtime_security_drift() -> None:
    module = _load()
    config = _config(module)
    pod = {
        "spec": {
            "automountServiceAccountToken": False,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 10001,
                "runAsGroup": 10001,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "runtime",
                    "image": config.analysis_image,
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                    },
                }
            ],
        }
    }
    module._validate_live_pod(config, pod)

    drifted = json.loads(json.dumps(pod))
    drifted["spec"]["containers"][0]["securityContext"][
        "allowPrivilegeEscalation"
    ] = True
    with pytest.raises(module.SmokeFailure, match="privilege escalation"):
        module._validate_live_pod(config, drifted)


def test_live_acceptance_preserves_primary_and_cleanup_failures(monkeypatch) -> None:
    module = _load()
    config = _config(
        module,
        live=True,
        confirmation=module.LIVE_CONFIRMATION,
    )
    monkeypatch.setattr(module, "_kubectl", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        module,
        "_wait_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            module.SmokeFailure("ready timeout")
        ),
    )
    monkeypatch.setattr(
        module,
        "_cleanup_claims",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            module.SmokeFailure("cleanup forbidden")
        ),
    )

    with pytest.raises(
        module.SmokeFailure,
        match="ready timeout.*additionally.*cleanup forbidden",
    ):
        module._live_acceptance(config)


def test_live_acceptance_exercises_health_isolation_ttl_and_cleanup(monkeypatch) -> None:
    module = _load()
    config = _config(
        module,
        live=True,
        confirmation=module.LIVE_CONFIRMATION,
    )
    config = module.Config(**{**config.__dict__, "namespace": "agent-eval-review"})
    kubectl_calls: list[tuple[str, ...]] = []
    run_calls: list[list[str]] = []
    exec_sources: list[str] = []
    absent: list[tuple[str, str]] = []

    def fake_kubectl(*args, input_text=None):
        del input_text
        kubectl_calls.append(args)
        return ""

    def fake_wait(resource, name, *_args, **_kwargs):
        if resource == "sandboxclaim":
            return {
                "spec": {"lifecycle": {"shutdownPolicy": "DeleteForeground"}},
                "status": {"sandbox": {"name": name.replace("ae-smoke", "sandbox")}},
            }
        return {
            "metadata": {"annotations": {"agents.x-k8s.io/pod-name": f"pod-{name}"}},
            "status": {
                "conditions": [{"type": "Ready", "status": "True"}],
                "serviceFQDN": f"{name}.omniagent-sandbox-staging.svc.cluster.local",
            },
        }

    def fake_exec(_namespace, _pod, source, **_kwargs):
        exec_sources.append(source)
        return "internal-health-ok" if "http.client" in source else "10001"

    def fake_run(command, **_kwargs):
        run_calls.append(command)
        if command[:2] == ["kubectl", "exec"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="blocked")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module, "_kubectl", fake_kubectl)
    monkeypatch.setattr(module, "_wait_json", fake_wait)
    monkeypatch.setattr(
        module,
        "_kubectl_json",
        lambda *_args: {
            "spec": {
                "automountServiceAccountToken": False,
                "securityContext": {
                    "runAsNonRoot": True,
                    "runAsUser": 10001,
                    "runAsGroup": 10001,
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "containers": [
                    {
                        "name": "runtime",
                        "image": config.analysis_image,
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
                            "readOnlyRootFilesystem": True,
                            "capabilities": {"drop": ["ALL"]},
                        },
                    }
                ],
            }
        },
    )
    monkeypatch.setattr(module, "_exec_python", fake_exec)
    monkeypatch.setattr(module, "_run", fake_run)
    monkeypatch.setattr(
        module,
        "_wait_absent",
        lambda resource, name, *_args: absent.append((resource, name)),
    )

    module._live_acceptance(config)

    create_calls = [call for call in kubectl_calls if call[:1] == ("create",)]
    patch_calls = [call for call in kubectl_calls if call[:2] == ("patch", "sandboxclaim")]
    delete_calls = [call for call in run_calls if call[:3] == ["kubectl", "delete", "sandboxclaim"]]
    assert len(create_calls) == 2
    assert all(
        call[1:3]
        == ("--as", "system:serviceaccount:agent-eval-review:omniagent-executor")
        for call in create_calls
    )
    assert len(patch_calls) == 1
    assert '"shutdownPolicy":"DeleteForeground"' in patch_calls[0][-1]
    assert any(
        "http.client" in source
        and "agent-eval-omniagent-internal.agent-eval-review.svc" in source
        and "'/health'" in source
        for source in exec_sources
    )
    assert absent[0][0] == "sandboxclaim"
    assert absent[1][0] == "sandbox"
    assert len(delete_calls) == 1
    assert "ae-smoke-b-" in delete_calls[0][3]
