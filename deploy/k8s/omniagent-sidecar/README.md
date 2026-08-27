# OmniAgent backend sidecar

本目录把 OmniAgent v1（`1.0.0a57+`）作为第二个普通容器追加到现有 `Deployment/backend` Pod。它不会替换 backend，也不会修改 `wait-for-postgres`、`db-migrate` 等 init container。两个容器共享 Pod 网络，因此 backend 使用：

```text
http://127.0.0.1:8090/api/agent/langgraph
```

本方案不创建 Service 或 Ingress，8090 不对 Pod 外暴露。本地 Docker Compose 仍使用独立 `omniagent` 服务和 `http://omniagent:8090/...`。

系统智能体前端只访问 agent_eval 后端，由 backend 使用专用 `OMNIAGENT_INTERNAL_URL` 代理 OmniAgent；该配置不复用被测智能体的 `target_agent.endpoint_url`。同 Pod 默认值为 `http://127.0.0.1:8090/api/agent/langgraph`，Compose 显式覆盖为 `http://omniagent:8090/api/agent/langgraph`。

## 前提

1. OmniAgent 镜像已推送到集群可拉取的 registry。不要依赖 agent_eval 仓库外的 `../OmniAgent` 构建上下文。
2. 当前 Kubernetes 对象默认为 namespace `agent-eval`、Deployment `backend`。
3. PostgreSQL 账号可访问独立数据库 `omniagent`；若不能自动建库，先由 DBA 创建。
4. 本机有 `kubectl`，且生产应用时具有 patch Deployment、apply ConfigMap/Secret 的权限。
5. 基础 sidecar patch 不改变 Pod 的 ServiceAccount。需要 analysis execution 时，再按
   `deploy/k8s/omniagent-execution/README.md` 显式应用 executor identity patch；这样仅部署
   聊天 sidecar 不会意外获得 SandboxClaim 权限。

## Secret

推荐由 Secret 管理系统创建名为 `omniagent-secret` 的 Secret，包含：

- `OMNIAGENT_API_KEY`
- `DB_PASSWORD`

`secret.example.yaml` 只有占位符，禁止填入真实值后提交。也可仅在执行时通过环境变量传入，`apply.sh` 会幂等创建/更新 Secret，且不会打印值。

## 离线渲染验证

不会连接或修改集群：

```bash
DRY_RUN=1 \
OMNIAGENT_IMAGE=aidong-backend.tencentcloudcr.com/aidong/omniagent:<tag> \
sh ./deploy/k8s/omniagent-sidecar/apply.sh
```

脚本会从 `deploy/omniagent/` 生成 ConfigMap，并把 strategic-merge patch 合并到一个含两个 init container 和 backend 容器的本地 fixture，确保 sidecar 追加而非覆盖。

## 首次应用

如果 Secret 已由运维创建：

```bash
OMNIAGENT_IMAGE=aidong-backend.tencentcloudcr.com/aidong/omniagent:<tag> \
DB_HOST=postgres \
DB_USER=postgres \
sh ./deploy/k8s/omniagent-sidecar/apply.sh
```

如果由脚本创建 Secret：

```bash
OMNIAGENT_IMAGE=aidong-backend.tencentcloudcr.com/aidong/omniagent:<tag> \
OMNIAGENT_API_KEY='<from-secret-manager>' \
DB_PASSWORD='<from-secret-manager>' \
sh ./deploy/k8s/omniagent-sidecar/apply.sh
```

可覆盖参数：`NAMESPACE`、`DEPLOYMENT_NAME`、`CONFIGMAP_NAME`、`SECRET_NAME`、`OMNIAGENT_MODEL`、`OMNIAGENT_BASE_URL`、`DB_HOST`、`DB_USER`、`ROLLOUT_TIMEOUT`。

ConfigMap 内容变化会改变 Pod template 上的 SHA-256 annotation，从而触发滚动更新。patch 同时设置 `kubectl.kubernetes.io/default-container: backend`，因此未指定 `-c` 的 `kubectl logs/exec` 仍默认进入 backend。

Kubernetes strategic merge 会按 `name` 合并 `env`。模板中删除某个环境变量不会自动删除 Deployment 里已存在的同名旧项；删除或重命名变量时，需要同时用 `kubectl edit` 或 JSON patch 清理旧项。

## CI/CD 接入

现有流水线只更新 backend 镜像，不会读取 Docker Compose 来修改 Kubernetes Pod。需要在流水线中增加一次 `apply.sh` 步骤，并显式传入可拉取的 `OMNIAGENT_IMAGE`。OmniAgent 与 backend 仍是两个独立镜像，不能只构建 backend 镜像。

## 验证

```bash
kubectl -n agent-eval get pods -l app=backend
kubectl -n agent-eval get pod <pod> -o jsonpath='{.spec.initContainers[*].name}{"\n"}{.spec.containers[*].name}{"\n"}'
kubectl -n agent-eval exec <pod> -c backend -- curl -fsS http://127.0.0.1:8090/openapi.json
kubectl -n agent-eval logs <pod> -c omniagent --tail=200
```

期望 Pod Ready `2/2`，init containers 仍包含 `wait-for-postgres db-migrate`，普通容器为 `backend omniagent`。

## 回滚

删除 sidecar 和配置卷时使用显式 JSON patch，避免误删 backend：

```bash
kubectl -n agent-eval edit deployment/backend
```

仅删除 `spec.template.spec.containers` 中 `name: omniagent`、`volumes` 中 `name: omniagent-config` 以及 `agent-eval.aidong.ai/omniagent-config-sha256` annotation。确认 rollout 后再按需删除 ConfigMap/Secret。
