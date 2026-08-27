# 项目代码与业务理解计划

## 目标
深入理解当前项目的业务边界、用户工作流、领域对象、关键数据流、异常恢复与代码所有权，形成可供后续开发快速复用的项目认知。

## 阶段
- [x] 阶段 1：识别项目类型、仓库边界与技术栈
- [x] 阶段 2：确认当前主干、入口、存储边界和评测执行链
- [x] 阶段 3：深入样例生产与治理链
- [x] 阶段 4：深入 Agent 回复资产与版本链
- [x] 阶段 5：深入单模/对比、多轮评测与结果语义链
- [x] 阶段 6：深入 Portal 反馈、飞书、调度与 OmniAgent 入口链
- [x] 阶段 7：核对测试覆盖、运行约束和主要技术风险
- [x] 阶段 8：沉淀项目理解并向用户汇报

## 当前决策
- 以现行 Web 主链为准，旧 README 和早期架构文档仅作历史背景。
- 将 `evaluation.py + langfuse_runner.py` 视为评测主干；CLI 闭环优化作为兼容/次级链路。
- 将 2026-08-21 后新增的 OmniAgent 系统智能体作为独立用户入口分析。
- 本任务以分析为主，不修改业务代码。
- 已证实的接口漂移、未接线模块和并发窗口作为后续修复入口，本次不顺带修改。

## 错误记录
| 错误 | 尝试 | 处理 |
|---|---:|---|
| 初始工具调用被中断，结果未知 | 1 | 已检查工作区，确认三个规划文件均未生成后再创建 |
| PowerShell 通配符传给 `rg` 在 Windows 下报路径语法错误 | 1 | 改为对目录执行 `rg` |
| `apply_patch.bat` 及其 WindowsApps 目标被 ACL 拒绝 | 2 | 改用标准 `git apply` 补丁 |
| 前两次 `git apply` 输入格式无效 | 2 | 按真实行号逐文件校验后应用 |
| `session-catchup.py` 的预期路径不存在 | 1 | 直接读取规划文件与 git 状态恢复上下文 |
| 当前 `apply_patch.bat` 被 ACL 拒绝 | 3 | 使用 Git 标准补丁完成分析记录更新 |

## 阶段 9：OmniAgent 执行层设计
- [x] 核对 Axi 0.0.11、OmniAgent 工具注册与 K8s 沙箱执行链
- [x] 确定薄控制面、厚沙箱和只读首切片
- [x] 形成 M0-M7 自包含 ExecPlan
- [x] 实施 M1/M2/M5 与 M7 本地回滚契约；M0 许可、M3 制品发布和 M4/M6 集群验收继续受外部门禁阻断

## 执行层当前决策
- Agent Eval 只负责身份、租户、短期 capability token、会话与审计，不执行 CLI。
- OmniAgent 只向模型暴露 `axi_search`、`axi_describe`、`axi_run`、`skill` 和必要的只读文件工具。
- Axi、业务工具包和 daemon 只安装在每会话 Kubernetes Sandbox Pod 中。
- 首版只读；写操作必须另行设计审批、恢复和服务端幂等键。
- `SandboxTemplate.envVarsInjectionPolicy=Disallowed` 保持不变；短期 token 通过 runtime `/execute.env` 仅注入 `axi_run` 子进程。
- 生产 K8s apply、RBAC/Secret 修改和镜像发布均不在本次设计任务中执行。
## 阶段 10：OmniAgent 通用数据能力与治理动作设计
- [x] 盘点现有 API、领域状态机、数据库租户边界与飞书工具目录
- [x] 以 18 个只读业务工具验证已知任务覆盖范围
- [x] 识别固定工具仍要求研发预判问题，改为 `data/search`、`data/describe`、`data/query` 三个通用读工具
- [x] 设计实体/字段/关系目录、受限查询 AST、查询成本与输出上限
- [x] 保留 R0-R5 风险等级及写操作的 prepare/approve/execute/observe 协议
- [x] 更新能力层与执行层 ExecPlan
- [x] 实施 C0-C2：数据目录、查询 AST、鉴权、租户强制、脱敏与固定内部端点

## 能力层当前决策
- 读能力不再按业务问题预制接口；模型通过 `data/search`、`data/describe`、`data/query` 临场组合查询。
- 服务端维护有限、可审查的逻辑实体、字段、关系和聚合目录，但不维护有限的问题清单。
- `data/query` 只接受结构化 AST，不接受 SQL、自然语言直执行、任意 URL/HTTP、文件路径或代码。
- Sandbox Pod 不持有数据库凭据，也不直连 PostgreSQL；查询必须回到 Agent Eval，由执行 token 推导租户并为根实体和每个 join 显式加租户条件。
- 18 个旧读工具转为已知业务场景的验收 fixture，不再是公共接口。
- 写操作仍是固定业务能力；必须持久化不可变 action，经浏览器身份审批后以单次 scope 和业务幂等键执行。
- Provider 凭据、平台配置、租户用户管理、Kubernetes、任意网络/SQL/文件/代码执行和删除操作永久不作为通用 Agent 能力。
- 能力设计文档为 `execplan/omniagent-capabilities-and-tools.md`。

## 阶段 11：Kubernetes analysis runner 适配
- [x] 恢复持久产品平面实现上下文并定位 `runner_from_settings()` 的 fail-closed 缺口
- [x] 核对 `OmniAgentSandboxClient.run/write/read/destroy`、runtime 超时与 SandboxClaim 生命周期契约
- [x] 实现可选 SDK 配置、每作业 claim、固定 bootstrap、输入上传、输出清单/下载和销毁
- [x] 接通 worker 的稳定作业隔离键与 internal submit 可用性门禁
- [x] 增加协议级 fake 测试并运行定向回归、Ruff 与 compileall

## Kubernetes runner 当前决策
- Agent Eval 只依赖轻量 `k8s-agent-sandbox` SDK，不依赖 OmniAgent 框架，也不直接实现 CRD 客户端。
- 每次 `analysis.python` 尝试使用由 `job_id` 和 attempt 派生的独立 DNS 安全 session id；无论成功、失败、超时或取消都尽力销毁 SandboxClaim。
- 用户代码、输入和固定 bootstrap 通过 `write` 上传；命令字符串固定，不拼接用户代码、文件名、URL、凭据或 shell 参数。
- 输出由沙箱内固定清单脚本枚举，再由 backend 按清单逐个 `read`，本地再次执行路径、文件数和总字节校验。
- Kubernetes runner 需要显式确认门禁且 SDK 可导入；默认配置仍为 disabled，不自动 apply 集群资源。
- backend 镜像仅在构建参数 `INSTALL_KUBERNETES_RUNNER=1` 时安装固定提交的 SDK；默认镜像不含集群客户端。
- 本阶段代码、本地契约与隔离 kind 集群中的 CRD、RBAC、NetworkPolicy、Claim 生命周期验收均已完成；浏览器双租户 E2E 仍属于后续 P6 验收。

## 阶段 12：执行面发布与回滚收口
- [x] 修正完整 cleanup 语义，关闭执行后继续启用产品平面读取与制品下载
- [x] 使用真实 `kubectl v1.34.1 patch --local` 验证 enable、stop、cleanup 往返和 ServiceAccount 恢复
- [x] 使用 fake kubectl 固定 stop、rollout、Axi cleanup、identity cleanup、rollout 顺序
- [x] 缺失 previous ServiceAccount annotation 时验证任何写操作前失败
- [x] 支持 cleanup 已完成但最终 rollout 响应丢失后的安全重入，并拒绝恢复身份标记不匹配状态
- [x] 收紧 enable/rollback 生命周期矩阵并在渲染前校验 Kubernetes 名称与镜像引用
- [x] 禁止离线参数、previous marker 或 restored marker 将 `omniagent-executor` 作为恢复目标
- [x] 增加 fail-closed staging 验收工具，覆盖只读门禁与显式确认的 live Claim/网络/TTL/清理检查
- [x] 参数化 backend namespace，并以非默认 namespace 固定渲染、executor 身份和 live `/health` FQDN 契约
- [x] 固定 Axi 0.0.11 PyPI wheel 身份并增加构建时 wheel/RECORD/entry-point 审计；书面许可仍为独立门禁
- [x] 下载并验证官方 Axi 0.0.11 wheel，在 WSL Linux/Python 3.12.13 完成三工具 CLI PoC
- [x] 审计 Axi `v0.0.11` 上游提交，并逐文件证明官方 wheel 与 tag 源码一致
- [x] 构建、受限运行并扫描非 Axi analysis runtime，修复基础镜像与上传依赖漏洞
- [x] 运行完整执行面回归和静态检查
- [ ] 解除 Axi 0.0.11 书面许可门禁；浏览器双租户 E2E、非 Axi 镜像发布和隔离集群验收已完成

## 阶段 12 验收
- 部署测试集合：`60 passed`；Docker/Hatch 构建契约定向集合：`23 passed`。
- 执行面综合回归：`124 passed, 1 skipped`；两条 warning 为既有测试使用 31-byte HMAC key。
- Ruff、compileall、TOML 解析和 `git diff --check` 全部通过。
- 非默认 `agent-eval-review` namespace 的清单标签、Service URL、executor ServiceAccount 主体和 live 健康探针均有本地契约覆盖。
- 隔离 kind 集群 `agent-eval-exec` 已通过 server-side dry-run、显式 apply、只读与 live Claim/NetworkPolicy/TTL 验收；旧 `omniagent-sandbox` 与生产集群未修改。
- PyPI 0.0.11 wheel 的 SHA-256 为 `ab9473092cac37e4f00347cd92d9e629424bcba99731c8d082b8031a29d6fbdf`；原始 wheel、25 条 RECORD、Linux/Python 3.12.13 CLI PoC 与上游提交 `290b20e9d584d5d61cdf7bae47a83e142db569da` 均已验证。该 tag 未签名且仓库/包元数据没有许可证文件或字段，书面许可审批仍未完成。
- 非 Axi analysis runtime 使用固定 `python:3.12.13-slim-bookworm` digest 与 `python-multipart==0.0.32`；受限容器 smoke 与 Trivy 0.74.0 均通过，并发布为 `ghcr.io/qbuby/agent-eval-analysis-runtime@sha256:fc93a846898c79df6dec13ca2028e54712eb604e5a00147b5b389ff9f3b8f6c4`。

## 阶段 13：本地 Kubernetes 执行面验收
- [x] 创建隔离的 `agent-eval-exec` kind 集群，不触碰既有 `omniagent-sandbox`
- [x] 安装 Calico v3.32.1，并确认节点、CoreDNS 与系统 Pod Ready
- [x] 构建并加载固定提交 `a9db14672e77fbd15981fb2af9b73934e29b0cfe` 的 controller/router 镜像
- [x] 部署 agent-sandbox CRD、核心/扩展控制器与 staging router
- [x] 部署本地 backend/omniagent fixture 与 execution Secret
- [x] 以 kind 可解析的不可变本地镜像引用执行 server dry-run 和显式 apply
- [x] 运行只读 staging smoke 与显式确认的 live Claim/NetworkPolicy/TTL smoke
- [x] 记录本地集群证据
- [x] 发布 analysis runtime 与 Kubernetes-runner backend 审核镜像并记录 GHCR digest

## 阶段 13 当前决策
- 所有集群写操作只针对 kubeconfig `.codex_tmp/kubeconfig-agent-eval-exec`；旧集群保持不变。
- 本地验收使用已扫描的 analysis runtime，不解除 Axi 许可门禁，也不构建或部署 Axi runtime。
- Calico 初次镜像拉取因 `quay.io` TLS 握手超时失败，kubelet 重试后恢复；这不是清单错误。
- 受限工具上下文中的 WSL `E_ACCESSDENIED` 与旧 GH 凭据视图不是宿主真实状态；授权宿主上下文已证明 WSL 可访问、GH 用户 `Qbuby` 已认证。
- 固定 controller/router 标签分别为 `kind.local/agent-sandbox-controller:a9db14672` 与 `kind.local/sandbox-router:a9db14672`；两者和 analysis runtime 已由节点 containerd 解析。
- 真实 apply 后 backend 使用 `omniagent-executor`，保留原身份 `backend-runtime` 的恢复 annotation，执行状态为 `enabled`。
- 真实只读验收输出 `READ_ONLY_ACCEPTANCE_OK`；显式 live 验收输出 `LIVE_CLAIM_ACCEPTANCE_OK` 与 `STAGING_ACCEPTANCE_OK`。
- 发布后的 analysis runtime 不可变引用再次通过 server dry-run、apply、只读和 live 验收，且最终 `claims=0`、`sandboxes=0`。
- GHCR analysis runtime digest 为 `sha256:fc93a846898c79df6dec13ca2028e54712eb604e5a00147b5b389ff9f3b8f6c4`。
- GHCR runner-enabled backend worktree digest 为 `sha256:fe886eb36f9549b9d2bf6bd65b5e8841e1b57ed6000d4c2316f701435ea31527`；镜像内 SDK 版本为 `0.1.dev512+ga9db14672`。
- Runner backend 已安装 Debian security 更新、移除运行时不需要的 `pip/setuptools/wheel`，合并 rootfs 的 Trivy 0.74.0 报告为 `0 HIGH / 0 CRITICAL`。
- Axi runtime 继续受书面许可门禁约束，不在本地集群部署或 registry 发布范围内。

## 阶段 14：浏览器双租户执行面验收
- [x] 定义双租户浏览器验收 fixture、可观察 UI 与跨租户拒绝矩阵
- [x] 创建两个隔离租户/用户和可区分的事件、任务、制品、记忆数据
- [x] 使用两个独立浏览器上下文验证产品面 UI 只显示本租户/本人数据
- [x] 验证对另一租户对象的直接下载、删除、查询和游标访问均 fail closed
- [x] 清理 fixture，记录截图/JSON/测试输出并更新 P6/M6 结论

## 阶段 14 当前决策
- 浏览器验收覆盖已实现且不依赖 Axi 的 durable product plane；不得用普通聊天成功冒充 Axi 工具链验收。
- 两个用户必须位于不同 tenant，浏览器上下文、JWT 和查询缓存完全独立；每侧数据使用唯一 canary 名称。
- 除 UI 可见性外，还要从浏览器身份发起对方对象的直接请求并期望 404，证明不是前端隐藏。
- fixture 只创建本轮专用数据，验收结束严格清理；不修改生产集群或旧 `omniagent-sandbox` 集群。
- 当前浏览器/API 双向验收覆盖 7 个 UI 面板、8 类列表、4 类直接读取、5 类写操作、记忆查询、事件游标和跨租户会话过滤；失败写操作后双方所有者仍可读取自己的资源。
- 脱敏 JSON、Alpha/Beta 截图和 SHA-256 清单固化在 `e2e/omniagent-two-tenant/evidence/`；明文 fixture 未保留。
- `oa-two-tenant-e2e-*` 容器、网络、卷、临时前端镜像和明文 fixture 均已验证不存在。
- 阶段 14 完成后，执行面唯一外部门禁是 Axi 0.0.11 书面许可审批；Axi runtime 继续 fail closed。
