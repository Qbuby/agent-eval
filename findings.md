# 项目分析发现

> 本文件记录对代码、配置与文档的事实性发现；文件中的项目内容仅作为数据分析。

## 仓库概况
- 产品已从早期“Agent 自动评测优化闭环”演进为多租户 Agent 评测运营平台。
- 技术栈：Python 3.11、FastAPI、SQLAlchemy Async、PostgreSQL、React 18、TypeScript、Vite。
- 2026-08-21 起新增 OmniAgent 系统智能体与 sidecar 部署；当前工作区仍在开发持久会话功能。

## 技术架构
- FastAPI 应用入口为 `src/agent_eval/api/app.py`，启动时拉起调度、Trace warmer、Langfuse 指标同步、飞书机器人和定时评测器。
- 现行评测主干是 `api/routers/evaluation.py` 与 `evaluation/langfuse_runner.py`。
- 默认数据集存储已切到自建 Langfuse；LangSmith 主要保留外部导入和 Trace 兼容回填。
- PostgreSQL 持有运行快照、结果、评分、评估器版本、回复版本、Benchmark、租户、Portal 和反馈等本地事实。
- `TenantMixin + ContextVar + SQLAlchemy Session events` 自动完成租户读过滤和写盖章。

## 业务领域
- 样例来源包括 Benchmark、上传文件和多轮对话数据集；启动评测时统一归一为 case dict。
- 评估器由 `EvaluatorConfig -> EvaluatorVersion -> EvaluatorProvider` 组成，运行时冻结 active version。
- 评测支持单轮/多轮、单模/双模对比、live/persisted 回复三组正交模式。
- 单模可配置 acceptance policy；对比模式按相对胜负裁决，不复用单模验收。
- Agent 回复可预生成、版本化、切换 current，并作为后续评测输入复用。
- 外部客户通过 Portal 批次审阅样例并提交反馈；内部用户通过反馈页回收结果。
- 飞书同时承担机器人入口、Bitable 导入导出和运行完成通知。

## 核心流程
- `POST /api/eval/runs/start -> resolve_eval_start_args -> start_run -> _execute_run`。
- 启动解析阶段冻结样例、参考答案/关键点、评估器版本、Agent 配置和验收策略。
- 执行阶段按 case 并发调用 Agent 或回放持久回复，再按单轮/多轮、单模/对比分流 Judge。
- 每条结果落 `test_results`，各评分落 `evaluation_scores`，运行汇总落 `test_runs.summary_scores`。
- 完成后异步触发飞书通知、LangSmith/Langfuse Trace 回填和可选 Langfuse 分数同步。
- 旧 `LoopController -> FailureAnalyzer -> StrategyGenerator -> StrategyApplicator` 仅由 CLI 调用，不是 Web 主航道。

## 风险与待确认项
- `langfuse_runner.py` 超过 3300 行，聚合执行、评分、落库、通知和外部同步，变更风险最高。
- `evaluation.py` 同时承担启动解析、运行查询、导出、补评及评估器 CRUD，边界较重。
- 后台 run 和后处理使用进程内 `asyncio.create_task`；重启会中断，非持久任务队列。
- Langfuse 外部数据不受 PostgreSQL `TenantMixin` 自动隔离，需要单独核对租户边界。
- 文档和注释仍有 LangSmith 主存储等旧表述，存在认知漂移。
- 当前未提交 OmniAgent 会话实现来自用户现场，只读分析，不做改动。

## 深入业务地图

### 样例资产
- Candidate 权威存储是 PostgreSQL `candidate_cases`，状态机为 `pending -> ready/rejected -> imported`；晋级 Benchmark 时复制问题、附件、答案、关键点和标签。
- Benchmark 权威存储是 PostgreSQL `benchmark_cases`；类别使用外键，Candidate 类别则是自由文本。
- Conversation 权威存储是 Langfuse dataset items，文件/Bitable 导入按样例名 upsert，并复用 Langfuse item ID；数据集删除由本地 `dataset_metadata.status=deleted` 模拟软删除。
- 三类资产的类别模型不一致：Candidate 自由文本、Benchmark 外键、Conversation 为 Langfuse metadata 中的受管名称。

### 回复与评测
- 回复资产由 `AgentReplyJob`、`AgentReplyJobItem`、append-only `AgentReplyVersion` 和 `AgentReplyCaseState.current_version_id` 组成；只有成功版本可自动成为 current。
- `PersistedReplyAdapter` 把历史回复适配为 Agent，复用单轮、多轮和对比评测链；评测结果固定保存 `reply_version_id`，被引用版本禁止删除。
- 评测严格区分 Agent 的 `execution_status`、Judge 的 `evaluation_status` 和策略层 `acceptance_decision`；未配置验收策略时不制造通过率。
- 单模支持阈值验收；对比模式只判 A/B 相对胜负，并随机交换展示位置后还原裁决；多轮同时支持逐轮期望和会话目标评分。
- 缺失 Judge 维度可补评并重算聚合，不需要重跑 Agent。

### 线上运营链
- LangSmith Trace 页面负责查询、详情、模型/首工具时延补全及向 Langfuse 数据集导入；短 TTL 内存缓存和 warmer 优化查询延迟。
- Langfuse metrics 服务按游标增量拉取 trace/observations，计算成本、token、时延和工具成功率后幂等 upsert 到 PostgreSQL，并可把 Trace 导入 Candidate。
- Trace watch 使用持久游标轮询 LangSmith；Routing 支持项目 glob、标签、metadata、状态和耗时条件，命中后抽取样例写目标数据集，并记录三次重试后的日志。
- Portal 是外部客户的 XLSX 批次评审入口，支持团队口径与个人口径；内部反馈页通过 service 映射层适配后端字段。
- 定时评测持久化与 `StartEvalRequest` 等价的 spec，到点复用正常评测解析和启动链；当前调度器按单实例设计。
- OmniAgent 是内部 Web 系统智能体，sidecar 通过 SSE 接入；当前工作区开发的会话按租户和用户隔离，并以稳定 thread、消息序号和单会话单飞收口中断状态。

## 已证实的实现断点
- **P0 飞书入口不可用**：长连接传 `union_id` 给不接收该参数的 `handle_text`；即便补过此处，`bot_service` 又把 `history/images/user_name` 传给不接收这些参数的 `run_orchestration`。运行时签名绑定已复现 `TypeError`。
- **P0 Trace 自动路由未接线**：启动只实例化 `SchedulerService`，从未构造 `RoutingSubscriber` 或订阅 `event_bus`；轮询会更新游标，但 routing 规则不会自动执行。
- **P1 Portal 上传契约漂移**：后端返回 `{batch, question_column, ...}`，前端声明为 `PortalBatch` 并读取顶层 `row_count`；成功提示会得到 `undefined`。前端允许 `.xls`，后端只接受 `.xlsx/.xlsm`。
- **P1 Benchmark 版本不是快照**：`create_version` 只写版本行和 `case_count`，随后执行的 case `select` 没有更新 `version_id`，版本与样例没有建立实际归属。
- **P1 回复并发窗口**：版本号使用“最大值 + 1”，唯一约束只负责报冲突，调用链没有 `IntegrityError` 重试；Job 计数也由并发 session 读后加一，可能丢更新并影响最终状态。
- **P1 配置安全与事务回归**：`llm.api_key` 不再属于敏感前缀；`batch_set` 已变为逐项提交，不再是单事务；`scheduler/routing` 新键会被归入 `general`。
- **P2 跨存储一致性**：Conversation 类别重命名先提交 PostgreSQL，再逐条更新 Langfuse，部分失败时会形成不一致。
- **P2 后台任务耐久性**：评测、回复生成、Routing 等主要依赖进程内 `asyncio.create_task`，重启后只能把遗留任务标记为 `interrupted/cancelled`，不能续跑。

## 代码所有权与修改入口
- 评测启动/查询/补评：`api/routers/evaluation.py`；执行、评分、聚合和后处理：`evaluation/langfuse_runner.py`。
- 样例治理：`api/routers/candidates.py`、`benchmark.py`、`datasets.py`、`cases.py`；外部 Trace 转换：`data/trace_extractor.py`。
- 回复资产：`api/routers/agent_replies.py`、`evaluation/reply_generator.py`、`db_models/repository.py`。
- 外围入口：Portal 在 `api/routers/portal.py` 与 `feedback_review.py`；飞书在 `feishu/`；Trace 调度和路由在 `scheduler/` 与 `routing/`；OmniAgent 在 `api/routers/omniagent.py` 与 `services/omniagent_chat.py`。
## OmniAgent 执行层设计发现（2026-08-24）
- OmniAgent 已具备 `ToolRegistry` provider、`BaseSandboxSession.execute(..., env=...)`、按 thread 复用 SandboxClaim、TTL 重建和技能同步，不需要新增一套 Pod executor。
- Axi 0.0.11 适合以 `search -> describe -> run` 做渐进式工具发现，但要求 Python 3.12；应只进入 sandbox runtime 镜像。
- Axi `run` 的业务失败可表现为退出码 0 加 `status=error`，wrapper 必须解析 JSON 信封，不能只看进程退出码。
- Axi daemon 不继承后续 CLI 进程的新环境变量；租户级 Agent Eval 工具首版必须使用 native tools，不能使用需要每请求凭据的 daemon MCP。
- Axi 仓库 README 声称 MIT，但未发现 LICENSE 或包 license 元数据；生产使用与镜像发布前必须完成许可门禁。
- `SandboxTemplate.envVarsInjectionPolicy` 只限制 `SandboxClaim.spec.env` 对 Pod 环境的创建期注入。SDK 会把命令级 `env` 放进 `/execute` JSON，请求运行时再合并到新子进程，因此模板可保持 `Disallowed`。
- 当前 runtime 会收集完整 stdout/stderr；只在 OmniAgent adapter 截断不足以防止内存和传输放大，runtime 本身也必须设输出上限并在超时/取消时杀整个进程组。
- Sandbox 代理会对基础设施错误重建并透明重试一次。只读首版可以接受；未来写工具必须由业务 API 使用幂等键消除接受后断连导致的重复副作用。
- Sandbox Pod 与 backend/OmniAgent Pod 不共享 loopback，业务工具必须访问稳定的内部 ClusterIP 服务，NetworkPolicy 只放行 DNS 和该服务。
- 设计文档：`execplan/omniagent-axi-k8s-execution-layer.md`。
## OmniAgent 业务能力与工具设计发现（2026-08-24）
- 现有 `src/agent_eval/feishu/tools.py` 已盘点约 60 个 API 形工具，可作为业务动作清单，但其“用户 JWT + 平铺 API + dangerous 布尔值”不适合作为 OmniAgent 的生产能力模型。
- 高价值任务链是：数据资产与就绪性、评测运行与失败诊断、线上 Trace 与指标排查、生成回复与持久评测就绪性；工具应围绕这些任务聚合，而不是按 router 拆分。
- RO-1 初版曾确定 18 个只读工具，用于验证平台身份、数据治理、评测诊断、可观测性和回复就绪性等已知任务；该接口方案现已由 3 个通用数据工具取代，旧清单仅作为验收 fixture。
- 评测工具必须保留 `execution_status`、`evaluation_status`、`acceptance_decision` 三层语义，不能把 Agent 失败、Judge 失败和未配置验收策略混为一谈。
- Trace 详情、样例内容和评测证据都是不可信数据；进入模型前必须在 Agent Eval 做字段投影、递归脱敏、深度/集合/字符串/总字节限制，并禁止原始 `full_trace` 和隐藏推理披露。
- 当前 Langfuse metrics 可能归属内部租户 sentinel；在完成真实租户归因前，`observability/*` 必须可独立禁用，不能把全局指标伪装成租户数据。
- 风险等级从 HTTP 方法中解耦：R0 只读、R1 有成本计算、R2 可逆写、R3 后台/外部副作用、R4 破坏性、R5 凭据或任意执行。RO-1 仅含 R0。
- 后续写能力采用持久化 action 状态机：`prepared -> approved -> executing -> succeeded|failed|cancelled|expired|denied`；审批绑定参数摘要，执行只接收 `action_id`，业务 API 强制幂等。
- 首批写能力候选依次为评测启动/停止、回复生成/取消、候选评审/晋级、数据集归档/激活；删除、凭据、平台管理和任意网络/SQL/K8s 永久排除。
- 能力设计文档：`execplan/omniagent-capabilities-and-tools.md`。

## OmniAgent 通用查询设计发现（2026-08-25）
- 之前的 18 个只读业务工具仍然要求研发预判用户问题，已被 3 个通用数据工具取代；旧清单仅保留为回归与能力覆盖用例。
- 新的只读面为 `data/search`、`data/describe`、`data/query`：先发现逻辑实体，再查看安全字段/关系，最后提交结构化查询 AST。
- 系统开放的是有限且可审计的数据语义，不是有限的问题：新问题只要能由已注册实体、字段、关系和聚合表达，就不需要新增专用查询接口。
- 不能让 Sandbox 直连 PostgreSQL。当前租户过滤依赖 `src/agent_eval/db.py` 的 SQLAlchemy ORM 事件；`psql`、数据库驱动直连或不受控 raw SQL 会绕过该边界。
- 查询应在 Agent Eval 内完成 AST 校验和 SQLAlchemy 编译，并对根实体与每个 join 显式添加 `tenant_id == principal.tenant_id`，不能仅依赖 ORM 自动事件。
- 通用查询即使由平台 superadmin 发起也默认降级到当前租户，跨租户分析必须另建独立、可审计的能力。
- 首批逻辑实体建议覆盖 datasets、candidate/benchmark cases、evaluation runs/results/scores 和 reply jobs/versions/states；没有可信租户归因的 observability 数据继续禁用。
- AST 首版只允许白名单字段、关系、过滤操作、分组、排序和聚合；禁止 SQL 片段、表达式语言、子查询、正则、字段间比较和用户指定 cast。
- 服务端硬限制包括关系深度/数量、选择列、过滤叶子、聚合数、返回行数、执行超时、响应体和每 turn 查询次数；超限在执行前失败。
- 写操作不通用化，继续使用固定业务动作、持久化审批、不可变参数摘要和服务端幂等键，以保护领域不变量。
- 通用查询实现切片采用服务端代码目录，不把物理表名、列名或生成 SQL返回给模型；首批查询硬限制固定为 20 个输出、20 个过滤叶、2 个分组、5 个聚合和 100 行。
- C0/C1/C2 首版只启用 PostgreSQL 中租户归属可靠的实体；Langfuse observability 与跨源 join 继续返回 `SOURCE_UNAVAILABLE`，直到真实租户归属和快照流程完成。

## OmniAgent 持久产品平面实施发现（2026-08-25）
- 浏览器身份与执行身份必须分离：浏览器 JWT 即使用同一签名密钥也会因 issuer、audience 和 token type 不匹配而被内部能力拒绝。
- execution token 绑定当前 assistant message，并只在真实登录用户且执行开关开启时签发；auth 关闭的开发旁路不会获得执行能力。
- 持久事件不应复制请求 SSE 的 token delta。消息仅在创建、开始生成和终态写事件，payload 只含状态、序号和工具名摘要。
- 文件系统制品适配器必须在 `resolve()` 前检查原路径是否为符号链接，否则解析后会丢失链接身份；作业输出发布还必须执行跨文件累计上限，而非只限制每个文件。
- `artifact_scanner=disabled` 必须产生 quarantined 状态而不是默认为 clean；本地 development scanner 必须显式开启。

## Kubernetes analysis runner 实现发现（2026-08-26）
- `OmniAgentSandboxClient` 位于独立 Apache-2.0 `k8s-agent-sandbox` SDK，不需要让 Agent Eval 依赖整个 OmniAgent 包。
- SDK 的同步接口为 `run(session_id, command, timeout, env)`、`write(session_id, path, bytes)`、`read(session_id, path)` 和 `destroy(session_id)`；首次 I/O 自动创建 SandboxClaim。
- SDK 可通过 `shutdown_after_seconds` 给每作业 claim 设置硬截止时间，适合 worker 崩溃后的集群侧兜底回收。
- runtime 的正常命令超时返回 exit code 124 并杀进程组；HTTP 读超时或连接中断不能证明命令未执行，因此 runner 不应重放 `/execute`。
- runtime 没有递归下载接口；安全做法是上传固定清单脚本，在沙箱内受限枚举 outputs，然后逐文件下载并在 backend 再校验相对路径、数量和总字节。
- internal submit 路由当前只允许 `runner == local_dev`，这是 Kubernetes 适配器接入后必须同步修正的可达性断点。
- SDK 的同步调用通过 `asyncio.to_thread` 接入；任务取消不会自动停止底层线程，因此 runner 必须在取消传播前等待 best-effort `destroy` 尝试结束。
- 输出清单和下载内容都属于不可信 sandbox 数据。backend 必须拒绝绝对路径、反斜杠、`.`/`..`、重复路径、非法大小、下载长度变化和本地符号链接父目录。
- claim 的 hard TTL 需要覆盖 sandbox ready timeout、最长分析时限和收尾窗口；默认 900 秒满足 `180 + 600 + 60` 秒下限。
- `RunnerInfrastructureError` 必须独立于确定性 `RunnerError`，才能复用 durable job 的基础设施重试而不重跑用户代码失败。
- Kubernetes SDK 以可选依赖固定到提交 `a9db14672e77fbd15981fb2af9b73934e29b0cfe`；Docker 默认不安装，显式 `INSTALL_KUBERNETES_RUNNER=1` 才进入 backend 镜像。

## 执行面回滚验证发现（2026-08-26）
- 完整关闭执行不能关闭产品平面；否则历史任务、制品元数据和下载路由会随 `product_plane_enabled=false` 一起不可用。
- Kubernetes strategic-merge 可在保留无关容器、env 和 annotation 的同时，按 env `name` 删除 execution 项并恢复原 ServiceAccount；本机 `kubectl patch --local` 已证明该往返。
- ServiceAccount 恢复依赖 enable 时写入的 `agent-eval.aidong.ai/omniagent-previous-service-account` annotation。当前身份已经是 executor 但 annotation 缺失时必须 fail closed，不能猜测 `default`。
- 两阶段 rollback 的安全顺序是：先禁用 token 签发和 worker 领取并 rollout，再禁用 Axi tool env，最后删除执行配置、恢复身份并再次 rollout。
- cleanup 后必须留下独立的 restored-ServiceAccount 审计标记，才能安全区分“最终 rollout 响应丢失”与手工或异常状态。只有 disabled、previous 已清、restored 与当前身份一致时可无 patch 重入；不一致时必须在 mutation 前失败。
- 发布状态不能仅凭单个 annotation 推断。Enable 只接受无执行标记的初始态、restored marker 与当前身份一致的完整 cleanup 态，或 `enabled + executor + previous marker` 的幂等态；rollback 只接受 `enabled|stopping + executor + previous marker` 或严格证明的完成态。
- 所有可进入 YAML 模板的 Kubernetes 名称与镜像引用必须在替换前校验；仅靠 `sed` 转义不足以阻止换行或引号改变 YAML 结构。
- `omniagent-executor` 永远不能成为 restoration target。离线参数、previous marker 和 restored marker 任一指向 executor 都必须在 mutation 前失败，否则被篡改的恢复证据可能保留执行权限。
- staging 验收必须把静态配置与实际运行态分开证明：只读档核对 CRD、server dry-run、最小 RBAC、精确模板/网络/Service/WarmPool；live 档以 executor 身份创建 Claim，并核对实际 Pod 安全、backend HTTP 健康、互联网和横向隔离、控制器 TTL 删除与资源清理。
- live smoke 的临时 Claim 同时使用 `DeleteForeground` 硬截止和严格 `finally` 清理；删除失败、资源未消失，或主验收失败后清理也失败，均必须非零退出并保留两类错误信息。
- 本地静态和协议验证不能替代 server-side dry-run、RBAC `can-i`、NetworkPolicy、claim TTL 与双租户 staging E2E；这些仍是发布门禁。
- backend namespace 不是固定的 `agent-eval`：离线清单中的 namespace/selector、内部 Service URL、executor ServiceAccount 主体和 live `/health` FQDN 必须由同一个已校验配置值生成；`agent-eval-review` 的本地契约现已覆盖整条链路。
- uv 缓存记录了 PyPI 官方 `axi_cli-0.0.11-py3-none-any.whl` 的来源 URL 和 SHA-256 `ab9473092cac37e4f00347cd92d9e629424bcba99731c8d082b8031a29d6fbdf`；缓存解包树 25 个文件与 25 条 RECORD 全部匹配。随后从该固定 PyPI URL 下载的 45,989-byte 原始 wheel 也通过同一 SHA、元数据、入口点、ZIP 安全和 RECORD 校验。
- Axi 0.0.11 的 METADATA 没有 `License` 字段，只有内嵌 README 正文写 `MIT`；制品身份校验不能替代书面许可审批。Windows 直接加载还会因无条件导入 Unix `fcntl` 失败，因此 CLI PoC 必须在 Linux/Python 3.12 中完成。
- Axi 0.0.11 从 `axi.native_tools` entry-point 自动发现原生工具；`agent-eval-axi-tools` 的 `data = agent_eval_axi_tools.data` 声明与该加载器契约一致，不需要在 `axi.json` 重复注册。
- WSL Ubuntu 的 CPython 3.12.13 实测 `axi-cli 0.0.11`：`doctor` 仅发现三个 `data` 工具；`search`、`describe`、成功调用、`FIELD_DENIED`、客户端超时和 malformed JSON 均返回预期 JSON。Malformed 参数是退出码 0 加 `status=error`；canary token 未出现在 stdout/stderr 或请求正文。
