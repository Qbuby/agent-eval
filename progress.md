# 分析进度

## 当前会话
- 已确认工作目录：D:\program\agent_eval。
- 已恢复既有规划文件，并把上次已完成的仓库级理解补录到磁盘。
- 已确认 2026-08-21 后新增 OmniAgent 集成，当前工作区存在未提交的会话功能开发。
- 已确认主评测调用链、数据所有权、多租户注入点及新旧主干边界。
- 正在执行阶段 3：深入样例生产与治理链。

## 文件变更
- 仅更新分析记录：task_plan.md、findings.md、progress.md。
- 未修改任何业务代码，也未触碰用户现有未提交实现。

## 验证记录
- 只读分析任务，不运行全量测试；后续以测试分布核对业务契约。

## 错误记录
- PowerShell 下将 `src/.../*.py` 通配符直接交给 `rg` 失败，已改用目录搜索。
- `apply_patch.bat` 因 WindowsApps ACL 被拒绝两次，改用 `git apply`。
- 两次手写补丁因输入/hunk 格式无效未落盘，随后按真实行号逐文件校验应用。

## 下一步
- 阅读 Benchmark/数据集/候选样例/生成/治理的端到端工作流。
- 阅读预生成回复的 job、item、version、case state 状态机。

## 深入分析完成
- 已完成样例资产、回复版本、评测语义、Portal、飞书、Trace 调度/路由、Langfuse metrics、定时评测和 OmniAgent 的端到端核对。
- 已确认两个 P0 断点：飞书两层函数签名漂移；RoutingSubscriber 未订阅 Scheduler event bus。
- 已确认 Portal 上传契约/扩展名漂移、Benchmark 假快照、回复并发计数窗口和配置服务回归。

## 最终验证
- Python 静态编译通过。
- Trace/数据/指标定向测试：`37 passed`。
- API 与评测测试组：`218 passed, 5 failed`；失败均在 `test_config_service.py`，其中 options-bag 存储断言属于测试滞后，敏感键、分类和 batch 事务语义属于实现回归信号。
- 前端 `npm run build` 成功，1193 modules transformed；仅有 SF 字体运行时解析和单 chunk 约 1.45 MB 的构建警告。
- 运行时签名检查复现：`handle_text(..., union_id=...)` 抛 unexpected keyword；`run_orchestration` 也不接受 `history/images/user_name`。

## 文件变更
- 仅修改分析记录 `task_plan.md`、`findings.md`、`progress.md`。
- 未修改业务代码，也未触碰用户当前 OmniAgent 开发文件。

## 错误补录
- `session-catchup.py` 预期路径不存在，改为直接读取规划文件和 git 状态恢复。
- `apply_patch.bat` 在普通与提升权限下均被 ACL 拒绝；多次 Git 手写补丁因 CRLF/hunk 计数失败未落盘，最终使用 PowerShell 文件 API 追加分析记录。
## 2026-08-24 OmniAgent 执行层设计
- 完成 Agent Eval、OmniAgent、Axi 与 agent-sandbox 的源码级边界核对。
- 将初稿审校并重排为 M0-M7：许可/CLI PoC、Axi wrapper、短期授权、runtime 镜像、K8s 隔离、业务工具与 Skill、E2E、渐进发布与回滚。
- 明确短期 token 只通过 `/execute.env` 进入 `axi_run` 子进程，不进入通用 sandbox env、prompt、工具参数、SSE 或日志。
- 明确首版只读，raw bash 与写文件工具不进入生产 allowlist。
- 明确 WSL Docker 只用于构建和镜像 smoke；SandboxClaim、WarmPool、RBAC 与 NetworkPolicy 必须在 Kubernetes 环境验证。
- 未修改业务代码、相邻 OmniAgent 源码或集群资源。

## 本轮验证与错误
- 核对客户端 `OmniAgentSandboxClient.run()` 会把命令级 env 发送到 `/execute`，runtime 为每次 subprocess 合并该 env。
- 核对 `envVarsInjectionPolicy: Disallowed` 只拒绝 SandboxClaim 创建期 env 注入，与上述命令级 env 不冲突。
- 一次 `rg` 组合正则因未闭合分组失败；已改为固定字符串查询并取得有效证据，未影响文件。
- ExecPlan 内容校验和 git diff 校验待本轮最后执行。
## 2026-08-24 OmniAgent 能力与工具设计
- 盘点数据集、治理、候选、基准、评测、回复版本、Langfuse metrics、Routing、Scheduler 和现有飞书工具实现。
- 将能力从 API 清单收敛为 18 个 RO-1 只读 Axi 工具、4 个业务 Skill 和 6 档风险模型。
- 设计固定内部 capability facade、字段投影、递归脱敏、游标分页、稳定错误码和每工具 scope。
- 设计后续写操作的持久化 action、浏览器审批、参数摘要、单次执行 scope、CAS 状态迁移和业务幂等键。
- 新建 `execplan/omniagent-capabilities-and-tools.md`，并同步 `execplan/omniagent-axi-k8s-execution-layer.md` 的 M5、示例工具名和 entry points。
- 未修改业务代码、OmniAgent 源码、镜像或 Kubernetes 资源。

## 本轮能力设计错误与处理
- 三条复杂 `rg` 查询分别因 PowerShell 参数解析和未闭合正则失败；已改用简单固定字符串/源码区段读取，失败结果未作为设计证据。
- 首次整篇 PowerShell 命令写文件触发 Windows error 206（命令行过长），未生成文件。
- 随后的 PTY `ReadToEnd` 方案无法接收 EOF；确认目标文件不存在后，仅终止本轮启动的 PID 42880。
- `Get-CimInstance Win32_Process` 因权限被拒；改用 `Get-Process` 按启动时间定位本轮子进程。
- 最终将文档拆成四个独立临时分片，内存合并校验后一次写入最终文件，并删除临时分片。

## 2026-08-25 OmniAgent 通用查询方向修订
- 根据用户预期，将只读能力从 18 个预制问题型工具改为 `data/search`、`data/describe`、`data/query` 三个通用工具。
- 重写 `execplan/omniagent-capabilities-and-tools.md`：加入逻辑实体目录、字段/关系/聚合白名单、结构化查询 AST、SQLAlchemy 编译器、资源上限、游标、审计和 source adapter 契约。
- 同步 `execplan/omniagent-axi-k8s-execution-layer.md` 的 Purpose、M5、验收、Axi 示例和 native entry point。
- 明确 Sandbox 不获得数据库凭据、不直连 PostgreSQL；所有查询在 Agent Eval 内强制租户条件并以只读事务执行。
- 原 18 个只读工具不再是实现接口，保留为已知业务问题的验收 fixtures；新增跨域、未预设问题验证通用组合能力。
- 写操作设计保持不变，仍为固定 prepare/approve/execute/observe 能力并要求服务端幂等。
- 本轮仅修改设计与工作记录，没有修改业务代码、镜像、OmniAgent 源码或 Kubernetes 资源。

## 本轮错误与处理
- `apply_patch.bat` 再次因本机 ACL 返回 Access denied，未修改文件；改用 PowerShell `.NET File` API 原子写入。
- 首次同步执行层时，示例块因预期字符串和实际 CRLF 文本不匹配而保护性中止，文件未写入；随后按 Markdown 章节边界替换成功。- 最终机械检查中，两次 PowerShell oreach 结果直接接管道触发 ParserError: An empty pipe element is not allowed；均为只读校验且未写文件。改为先收集数组再输出后检查通过。
- 首轮关键词检查因只匹配固定英文短语，将语义等价表述误报为两项 False；随后按实际文档措辞复核，确认“无专用接口的新问题”和“禁止直接 SQL/使用 typed query AST”均已明确写入。
## 2026-08-25 OmniAgent 持久产品平面实施
- 已新增十类 ORM 记录与冻结的 Alembic 0043；历史迁移不再导入当前 `Base.metadata`。
- 已实现独立 execution JWT、预算只能收紧、scope 检查、固定摘要和任务/动作状态机。
- 已实现持久事件、任务领取、心跳、失租拒绝、基础设施错误最多重试、动作 prepare/approve/deny 和显式个人记忆基础服务。
- 修复动作验证器可变默认参数，以及中文记忆按字符而非 UTF-8 字节计额的问题。
- 定向测试 `15 passed`；Alembic 识别 `0043 (head)`；运行时模块静态编译通过。
- Axi 0.0.11 需要 Python 3.12，GitHub 仓库无 LICENSE 文件且包元数据无 license 字段；集成继续保持关闭，生产再发布受许可门禁阻断。
- 当前进入 P2：浏览器持久事件/任务/审批/记忆 API、execution JWT 内部 API，以及仅开关启用时的聊天令牌注入。
- P2/P3 后端纵向切片已接入：本人事件/任务/审批/记忆/制品/通知 API，execution JWT 内部 API，受限本地分析 runner、持久 worker、固定动作执行、Outbox 与结构化计划基础；当前定向验证为 `27 passed, 1 skipped`。
- 现在实施 C0-C2：逻辑实体目录、发现/描述、受限查询 AST 与显式租户 SQLAlchemy 编译器。

## 2026-08-25 OmniAgent P2 与 P3 进度
- P2 已完成：新增本人范围的事件、任务、审批和记忆浏览器 API，以及仅接受 execution JWT 和 scope 的内部 API。
- 聊天 payload 在执行开关关闭时保持原结构；开启且存在真实登录用户时，令牌绑定 tenant、user、session 和当前 assistant message，执行 principal 永远 `superadmin=false`。
- 聊天创建与终态现在写低频持久事件，不持久化正文、工具参数或工具结果。
- 已通过运行时测试 `15 passed`；聊天核心前七项通过；Ruff 与 compileall 通过。当前 Windows Python 3.14 的全应用 OpenAPI 生成异常缓慢，路由验收改为直接检查注册路由。
- P3 已开始：`artifacts.py` 已实现文件名、对象键、路径逃逸、符号链接、大小、SHA-256、MIME/magic、UTF-8、XLSX 容器和扫描 fail-closed 校验，并限制单作业输出总量。
- 本轮被中断时 `runner.py` 尚未创建；恢复检查确认无后台终端会话，临时候选 `app.py.codex-candidate` 与真实文件哈希相同。

## 2026-08-25 OmniAgent 扩展执行能力完成情况
- 已完成任务租约、固定动作执行、分析作业、制品、显式记忆、配额、通知 Outbox 和结构化计划的后端闭环，所有新执行开关默认关闭。
- 已完成九个逻辑实体的 `data/search|describe|query`，AST 不接收 SQL/URL/物理表名，根实体和关系目标均显式注入 execution token 租户。
- 已完成 OmniAgent 页面运行侧栏：活动、审批、附件、记忆、通知、计划；宽屏三栏、窄屏抽屉。
- 已创建独立 Axi native 包、七个审核技能、license-gated runtime Dockerfile 与 replicas=0 的 Kubernetes staging 清单；生产工具白名单仍保持零工具。
- 验证：focused backend `47 passed, 1 skipped`，独立客户端与部署测试通过，前端 production build 通过，Alembic 数据库版本为 `0043`。
- WSL Docker 已重建 backend/frontend/OmniAgent，四个核心容器持续 healthy 超过一分钟，backend `/health`、frontend `/`、OmniAgent `/openapi.json` 均为 HTTP 200。
- 当前机器 WSL 在无前台 Linux 进程时会停止发行版并带停容器；需要隐藏 keepalive 或由宿主常驻方式维持 Docker 服务。

## 2026-08-26 Kubernetes analysis runner 续接
- 已恢复原会话并确认剩余实现是生产 analysis runner，而不是重新设计产品平面。
- 已核对本机 `k8s_agent_sandbox.OmniAgentSandboxClient` 的 `run/write/read/destroy` 契约；SDK 使用每 session 一个 SandboxClaim，并支持 hard shutdown TTL。
- 已确认 runtime 在服务端命令超时时返回 exit code 124 并终止整个进程组；传输层超时仍不能安全重放，因此 runner 对一次执行不做透明 retry。
- 已确认 internal `analysis/submit` 当前硬编码只接受 `local_dev`，需要与 runner factory 一并修正。
- 已完成 Kubernetes runner：每作业尝试独立 SandboxClaim、固定脚本上传、固定命令执行、输入上传、输出清单逐文件下载、路径/数量/字节复核，以及成功/失败/超时/取消后的 claim 销毁。
- worker 现在用 `job_id + attempt` 生成稳定隔离键，并将 SDK/控制面故障送入既有基础设施重试；代码失败、超时、日志超限和输出违规保持确定性失败。
- internal `analysis/submit` 已改用统一 runner 可用性门禁；默认 disabled，Kubernetes 还要求显式 review confirmation、SDK、namespace/template 和覆盖就绪+执行+收尾的 TTL。
- backend SDK 依赖固定到 OmniAgent 已验证提交，并通过 `INSTALL_KUBERNETES_RUNNER=1` 选择性安装；默认镜像行为不变。
- 当时的 Kubernetes runner 定向回归已通过；该历史子集结果已由当前更完整的 `106 passed, 1 skipped` 基线取代。Ruff、compileall、TOML 解析和 whitespace 检查均通过；两条 JWT 警告来自测试用 31-byte HMAC key，与实现无关。
- 首轮 pytest 因系统 `%TEMP%/pytest-of-frh` ACL 拒绝访问而产生 8 个 setup error；改用仓库内 `--basetemp` 后全部通过，未重复误判为代码问题。
- 未连接 kubeconfig、未创建 SandboxClaim、未 apply Kubernetes 对象。真实 staging server dry-run、RBAC/NetworkPolicy 和生产 sandbox E2E 仍待外部集群与许可/镜像门禁解除。

## 2026-08-26 执行面发布与回滚收口
- cleanup patch 不再删除 `OMNIAGENT_PRODUCT_PLANE_ENABLED`，而是显式保持 `true`；执行、worker、runner、execution secret/allowlist 与扫描配置仍被移除。
- `apply.sh` 的 server dry-run 与 apply 使用相同的 stop -> optional Axi cleanup -> identity/config cleanup 顺序；真实 apply 在 stop 和 cleanup 后分别等待 rollout。
- enable 时记录原 ServiceAccount；rollback 缺失该 annotation 时在 apply/patch/rollout 前失败；cleanup 恢复原 ServiceAccount 并删除记录 annotation。
- 本机 `kubectl v1.34.1 patch --local` 往返测试证明无关 backend/omniagent 容器、env 和 annotation 均保留。
- cleanup 额外记录 restored ServiceAccount；若最终 rollout 响应丢失，重跑只在 execution=disabled、previous marker 已清且 restored marker 与当前身份完全一致时恢复 rollout 观察，否则零 mutation 失败。
- 后续 enable 会删除 restored marker，避免旧恢复证据跨启用周期残留。
- enable 仅接受初始态、完整 cleanup 态或严格一致的已启用幂等态；rollback 仅接受 `enabled|stopping + executor + previous marker` 或可证明的 completed cleanup 态。
- Namespace、Deployment、Secret、ServiceAccount 和不可变镜像引用在模板替换前经过保守字符校验，拒绝换行、引号和额外 digest marker。
- 离线 `ORIGINAL_SERVICE_ACCOUNT`、previous marker 和 restored marker 均禁止使用 `omniagent-executor`，避免回滚保留执行权限。
- 新增 `staging_smoke.py`：默认只读检查 context、CRD、server dry-run、RBAC、精确模板/NetworkPolicy/Service/WarmPool；live 模式要求精确确认后以 executor 身份创建两只短期 Claim，验证实际 Pod、backend `/health`、互联网与横向隔离、控制器 TTL 删除及严格清理。
- staging smoke 的本地契约覆盖配置拒绝、确认门、Claim 生命周期、executor 创建身份、Pod 安全、网络、TTL、严格清理及主故障/清理故障合并报告。
- 部署测试集合 `43 passed`；完整 runtime/API/deployment 回归 `106 passed, 1 skipped`。
- Ruff、compileall、TOML 和 `git diff --check` 均通过。两条 JWT warning 来自既有测试的 31-byte HMAC key。
- 当前 kubeconfig 没有 current-context；staging smoke 稳定 fail closed，未创建 Claim，未执行真实 server-side dry-run/live 验收或 apply，也未构建、扫描、推送 runtime 镜像。

## 2026-08-26 backend namespace 契约收口
- 修正离线渲染测试对 quoted YAML namespace 的陈旧断言；实现已正确输出 `namespace: "agent-eval-review"`。
- 核对 live smoke 的 backend 健康探针已使用 `config.namespace`，并在完整 live 流程测试中以非默认 `agent-eval-review` 同时固定 executor ServiceAccount 主体和 `/health` FQDN。
- 定向部署测试 `31 passed`；完整部署集合 `43 passed`；执行面综合回归 `106 passed, 1 skipped, 2 warnings`。两条 warning 仍来自既有 31-byte 测试 HMAC key。
- Ruff、compileall、`sh -n`、多文档 YAML/TOML 解析和 `git diff --check` 均通过。首次 YAML 校验误用单文档 `safe_load`，改用 `safe_load_all` 后通过，清单本身无错误。
- 重新审计外部门禁：`kubectl` 仍未设置 current-context，Docker daemon 仍不可达，本地两份 Axi checkout 均为 `0.0.10`；因此没有执行 Claim、server dry-run/live staging、镜像构建/扫描/推送或生产 apply。

## 2026-08-26 Axi wheel 供应链门禁
- 在 uv 缓存中发现 PyPI `axi-cli 0.0.11` 解包树与来源元数据，确认官方 wheel SHA-256 为 `ab9473092cac37e4f00347cd92d9e629424bcba99731c8d082b8031a29d6fbdf`；缓存树 25 个文件与 RECORD 全部一致。
- 新增 `deploy/omniagent/execution-runtime/verify_axi_wheel.py`，固定官方文件名、来源 URL 和 SHA，校验 ZIP 路径、大小、压缩策略、METADATA、Python 版本、wheel tag、console entry point 及整份 RECORD。
- Axi runtime Dockerfile 现在保留 wheel 官方文件名，并要求 `CONFIRM_AXI_LICENSE_REVIEWED=axi-license-reviewed` 与 wheel 验证器同时通过；wheel 通过 `.gitignore` 明确禁止提交。
- 核对 Axi 0.0.11 实际通过 `axi.native_tools` entry-point 自动发现工具，仓库自有包声明与加载器一致。
- Windows 上直接加载缓存包因 `fcntl` 不存在而失败；当前环境也缺部分 Axi 依赖。该结果不是 CLI 验收，M0 仍需 Linux/Python 3.12 PoC、原始 wheel 和书面许可审批。
- 从固定 PyPI URL 下载 45,989-byte 官方 wheel 到 Git 忽略的本地 `vendor/` 目录；验证器确认 SHA-256、25 个成员、元数据、入口点和 RECORD 全部通过。
- 在 WSL Ubuntu 的 CPython 3.12.13 隔离环境安装官方 wheel 与 `agent-eval-axi-tools`；`axi doctor` 仅发现 `data/search`、`data/describe`、`data/query`。
- 新增 loopback-only `scripts/axi-poc/run_smoke.py`。真实 CLI PoC 验证成功调用、`FIELD_DENIED`、`QUERY_TIMEOUT`、malformed `status=error` 和 token 不泄漏。M0 现在只剩书面许可审批与 reviewed upstream commit。

## 2026-08-26 Axi 来源审计与 runtime 镜像收口
- GitHub `v0.0.11` tag 指向未签名提交 `290b20e9d584d5d61cdf7bae47a83e142db569da`、tree `5ddd9f7b9c7e2b3db6e43fb1f463bb231827bff3`；官方 wheel 中 21 个 `axi/*.py` 与该提交 21 个 `src/axi/*.py` Git blob 全部一致，没有缺失、改写或额外 Python 模块。
- tag 没有 GitHub Release；提交树和 PyPI 元数据均没有 `LICENSE`、`COPYING`、`License`、`license_expression` 或 `license_files`，只有 README 正文声明 MIT，因此书面许可审批继续独立阻断 Axi 镜像构建。
- Axi runtime Dockerfile 的真实无确认构建在安装前 fail closed，并输出明确的书面许可确认错误；修正了 wheel 默认路径必须相对仓库根 build context 的问题。
- WSL Docker 构建了非 Axi `agent-eval-analysis-runtime:codex-smoke`，并在只读根、drop-all capabilities、no-new-privileges、PIDs/内存限制和 tmpfs 下通过 UID/GID、健康、上传下载、命令级环境隔离、超时退出 124、进程组清理与路径逃逸 smoke。
- 首次 Trivy 0.74.0 扫描旧 Debian 13.1/Python 3.12.11 镜像发现 `64 HIGH / 3 CRITICAL` OS 漏洞和 3 个 `python-multipart` HIGH。改用固定 `python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2` 并升级到 `python-multipart==0.0.32` 后，最终报告为 `0 HIGH / 0 CRITICAL`。
- 当前本地 analysis runtime manifest-list digest 为 `sha256:fc93a846898c79df6dec13ca2028e54712eb604e5a00147b5b389ff9f3b8f6c4`，仅是本机 smoke 标签，不得作为 registry 发布 digest。
- 最新部署回归 `55 passed`；完整执行面回归 `118 passed, 1 skipped, 2 warnings`。Ruff、compileall、shell 语法、多文档 YAML/TOML 和 whitespace 均通过。
- 工作区曾由外部流程切到 `codex/mirror-source-only`；执行面已安全提交在 `1cf3d39`。已无损切回 `feat/traces-model-fill`，没有重置、cherry-pick、rebase 或覆盖外部提交。

## 2026-08-26 本地 Kubernetes 执行面续接
- 新建隔离 kind 集群 `agent-eval-exec`，使用 Kubernetes v1.35.0 与独立 kubeconfig `.codex_tmp/kubeconfig-agent-eval-exec`；既有 `omniagent-sandbox` 未修改。
- Calico v3.32.1 首次从 `quay.io` 拉取 `cni` 镜像时发生一次 TLS handshake timeout，随后 kubelet 自动重试成功；`calico-node`、`calico-kube-controllers`、CoreDNS、local-path-provisioner 与节点现均为 Ready。
- 授权宿主上下文确认 WSL Docker 29.5.3 可用，所需本地镜像仍存在；Windows Docker Desktop daemon 未运行不影响 WSL Docker。
- 授权宿主上下文确认 `gh auth status` 为用户 `Qbuby` 登录成功；先前失败输出来自读取不到 keyring/config 的受限进程视角。
- 下一步构建固定 agent-sandbox 提交的 controller/router，加载至 `agent-eval-exec`，然后部署 execution fixture 并运行真实 staging smoke。

## 2026-08-26 本地 Kubernetes 执行面验收完成
- 从 agent-sandbox 固定提交 `a9db14672e77fbd15981fb2af9b73934e29b0cfe` 构建并加载 controller/router；部署四个 CRD、带 extensions 的 controller 和单副本 router，全部 Ready。
- 将已扫描的 analysis runtime 加载到 `agent-eval-exec`，并通过节点 containerd 为不可变 digest 引用建立可解析别名。
- 部署本地 `agent-eval/backend` 双容器 fixture、`backend-runtime` 原始身份及 execution Secret；server-side dry-run 与显式 `MODE=apply` 均成功，backend 切换到 `omniagent-executor` 后 `1/1 Ready`。
- 真实只读验收通过：`READ_ONLY_ACCEPTANCE_OK context=kind-agent-eval-exec`、`STAGING_ACCEPTANCE_OK`。
- 真实 live 验收通过：两只短期 Claim Ready，运行时 UID/安全上下文、backend `/health`、公网拒绝、横向拒绝、TTL 删除和 finally 清理全部成功；输出 `LIVE_CLAIM_ACCEPTANCE_OK`。
- 验收发现并修复两个产品兼容缺口：annotation key 改用缺失时返回空串的 Go template `with index`；`kubectl auth can-i` 接受不同版本的 `no` 退出码 0/1，同时继续对控制面错误 fail closed。
- 更新后的 execution/staging 部署测试为 `36 passed`，shell 语法、Python 编译和 whitespace 检查通过。
- 旧 `omniagent-sandbox` 集群从未修改；Axi runtime 未构建、未部署、未解除许可门禁。

## 2026-08-26 执行面制品发布完成
- GH CLI 已补充最小 `write:packages` scope；凭据只通过 stdin 交给 WSL Docker，未写入项目文件或日志。
- 非 Axi analysis runtime 已发布为 `ghcr.io/qbuby/agent-eval-analysis-runtime@sha256:fc93a846898c79df6dec13ca2028e54712eb604e5a00147b5b389ff9f3b8f6c4`，发布 digest 与本地受限容器/Trivy 验收制品一致。
- runner-enabled backend 构建暴露并修复两处真实缺口：Hatch 未允许固定 Git direct reference，以及镜像缺少用于 checkout 固定 SDK 提交的 Git。构建实际解析到 `a9db14672e77fbd15981fb2af9b73934e29b0cfe`，安装包版本为 `0.1.dev512+ga9db14672`。
- backend 镜像安装 Debian security 更新，并在依赖安装及 `pip check` 后移除运行时不需要的 `pip`、`setuptools`、`wheel`；真实应用导入和 `/health` smoke 通过。
- runner backend 已发布为 `ghcr.io/qbuby/agent-eval-backend@sha256:fe886eb36f9549b9d2bf6bd65b5e8841e1b57ed6000d4c2316f701435ea31527`。Trivy 0.74.0 对合并 rootfs 的 OS 与 Python 包扫描为 `0 HIGH / 0 CRITICAL`。
- 发布后的 analysis GHCR digest 再次通过隔离 kind 的 server-side dry-run、显式 apply、只读和 live Claim/NetworkPolicy/TTL 验收；终态为 `claims=0`、`sandboxes=0`、node Ready、backend `omniagent-executor/enabled/1/1`。
- 最终执行面回归为 `124 passed, 1 skipped, 2 warnings`；两条 warning 仍只来自既有测试用 31-byte HMAC key。Ruff、compileall、shell 语法、TOML 与 `git diff --check` 通过。

## 2026-08-26 浏览器双租户 E2E
- 新增独立 `oa-two-tenant-e2e` 临时栈、ORM fixture、API 隔离检查和浏览器验收脚本；临时栈使用独立 PostgreSQL、网络、制品卷及 `18082/18083` 端口，不修改常规 Compose 或 kind。
- 首次播种因 Tenant/User/Session/Action 没有 ORM relationship 可供 SQLAlchemy 推导插入顺序，action 在 user 之前 flush，触发 `omniagent_actions_requested_by_fkey`；启动脚本的失败 trap 已自动清理全部临时资源。
- fixture 现在在 tenant、user、session 和 action 外键层之间显式 flush，确保可重复播种顺序。

## 2026-08-26 浏览器双租户验收续接
- 阶段 13 的本地集群、不可变镜像与发布验收已完成；当前唯一可自主推进的执行面门禁是浏览器双租户 E2E。
- 现有 `verify-212-omniagent-chat.mjs` 只证明单用户会话隔离，不能证明 durable product plane 的租户隔离。
- 双租户验收将使用两个独立浏览器上下文，验证活动、任务、制品、记忆的 UI 可见性以及对方对象直接访问返回 404；Axi 工具问答链继续受书面许可门禁。

## 2026-08-27 浏览器双租户验收收口
- 修复 fixture 的显式 flush 顺序，并把启动就绪门槛改为专用网络内 PostgreSQL 客户端探测；backend 提前退出时立即输出日志，失败路径继续自动清理。
- API 双向验收通过：8 类列表隔离、4 类直接读取 404、5 类写操作 404、记忆查询隔离、事件游标与跨租户会话过滤隔离。
- 系统 Chrome 双浏览器上下文验收通过：Alpha/Beta 各自 7 个 UI 面板只显示自身 marker；跨租户写操作后双方资源仍由所有者可见。
- 脱敏 JSON、两张截图与 SHA-256 清单固化到 `e2e/omniagent-two-tenant/evidence/`；`.codex_tmp/` 已加入忽略规则。
- `stop.sh` 后验证专用容器、网络、卷、临时前端镜像均不存在，明文 fixture 已删除。
- 完整执行面回归：`124 passed, 1 skipped, 2 warnings`；两条 warning 仍来自既有 31-byte 测试 HMAC key。Ruff、compileall、shell 语法和 `git diff --check` 通过。
- P6/M6 与阶段 14 已完成；Axi 0.0.11 书面许可是唯一剩余外部门禁，Axi runtime 继续 fail closed。
