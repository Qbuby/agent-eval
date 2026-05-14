<div align="center">

# Agent Eval

**Agent 自动化测试与优化闭环系统**

面向 LangChain / LangGraph Agent 的全链路 Trace 采集、多维度评估与策略驱动优化平台。

`Python 3.11+` · `FastAPI` · `PostgreSQL` · `LangSmith` · `React 18` · `BFCL-v4`

</div>

---

## 概览

Agent Eval 把 Agent 的「能力回归测试」从人工抽查变成可度量、可复现、可自动收敛的工程流程。

系统围绕三件事展开：

| | |
|---|---|
| **采** | 通过 LangChain Callback 或 HTTP Adapter 零侵入采集 Agent 的推理步骤、工具调用、延迟与 Token 开销 |
| **评** | 五维评分体系（输出正确性 · 工具序列 · 推理质量 · 性能 · 错误恢复）+ LLM-as-Judge，支持规则 / LLM / 混合三种模式 |
| **优** | 失败模式聚类 → LLM 生成策略 → 应用到 Agent 配置副本 → 回归对照。三重安全阀（收敛、停滞、回归）兜底 |

数据集托管在 LangSmith，全流程可通过 **CLI**、**FastAPI 后端** 和 **React Web UI** 三种接口驱动。

---

## 架构

```
┌──────────────────────────────────────────────────────────────────┐
│                  LoopController  —  闭环控制器                    │
│         收敛判断 · 停滞检测 · 回归保护 · A/B 分流                  │
└──────┬──────────────────┬──────────────────┬─────────────────────┘
       │                  │                  │
       ▼                  ▼                  ▼
┌──────────────┐  ┌────────────────┐  ┌───────────────────┐
│   数据层      │  │    评估层       │  │     优化层         │
│              │  │                │  │                   │
│ DatasetMgr   │→│ TraceCollector │→│ FailureAnalyzer   │
│ CaseGenerator│  │ 5 × Scorer     │  │ StrategyGenerator │
│ TraceExtract │  │ RegressionDet. │  │ StrategyApplicator│
└──────────────┘  └────────────────┘  └───────────────────┘
       │                  │                  │
       └──────────────────┼──────────────────┘
                          ▼
              ┌─────────────────────┐
              │     PostgreSQL      │
              │      + LangSmith     │
              │       + Grafana      │
              └─────────────────────┘
```

### 闭环迭代流

```
加载用例 → 执行 Agent → 采集 Trace → 五维评分
    ↑                                     │
    │                               达标? ──→ 是 → 输出报告
    │                                 │
    │                                 否
    │                                 ▼
    └── 补充用例 ← 失败分析 → 生成策略 → 应用策略 → 重新测试
```

每轮迭代：加载 → 并发执行 → 采 Trace → 五维打分 → 收敛判断 → 回归检测 → 停滞检测 → 失败聚类 → LLM 生成策略 → 策略应用 → 下一轮。

---

## 功能矩阵

### 数据层

| 能力 | 说明 |
|------|------|
| 数据集 CRUD | LangSmith 托管，支持 tag / split / as-of 版本快照 |
| 用例导入 | JSON / JSONL 文件、LangSmith 线上 trace、外部 LangSmith 数据集同步 |
| 用例生成 | `generate scenario` 从场景描述生成、`generate mutate` 从现有用例变异（rephrase / edge_case / adversarial） |
| Benchmark 导入 | Excel / CSV 批量导入标准数据集 |
| 数据路由 | 不同数据集路由到不同评估方法，匹配器驱动 |

### 评估层

| 维度 | 默认权重 | 评分方式 | 关键逻辑 |
|------|---------|---------|---------|
| 输出正确性 | 0.30 | 规则 + LLM | 关键词 / 正则匹配（0.4）+ LLM 语义判定（0.6） |
| 工具调用序列 | 0.25 | 规则 | 覆盖率 0.35 + LCS 顺序 0.25 + 参数匹配 0.25 + 冗余惩罚 0.15 |
| 推理质量 | 0.20 | LLM Judge | 逻辑连贯 · 信息利用 · 幻觉检测 · 推理效率 |
| 性能 | 0.15 | 规则 | 延迟 / Token / 调用次数对阈值的比值 |
| 错误恢复 | 0.10 | 规则 + LLM | 重试检测（0.4）+ 恢复策略合理性（0.6） |

评分器全部继承 `DimensionScorer` 基类，可按需增减维度。支持的 Agent 接入方式：

- **Python Factory** — 实现 `AgentFactory` 协议（`get_config()` / `create(config)`）
- **OpenAI-Compatible API** — 任意兼容 `/v1/chat/completions` 的 HTTP 端点
- **SSE Stream** — 自定义事件流协议，通过 `payload_template` 配置
- **BFCL-v4** — 接入 Berkeley Function Calling Leaderboard 作为标准化基准

### 优化层

失败用例（总分 `< 0.7`）进入九类预定义失败模式：`tool_selection_error` · `tool_param_error` · `missing_tool_call` · `redundant_tool_call` · `reasoning_error` · `hallucination` · `instruction_following` · `context_loss` · `error_handling`。

策略类型：`prompt` · `tool_config` · `system_param` · `composite`。每条变更包含 `change_type` / `target` / `before` / `after` / `reason`，按风险等级（low / medium / high）标注。

### 闭环控制

| 参数 | 默认 | 作用 |
|------|------|------|
| `target_score` | 0.85 | 达标即收敛 |
| `max_iterations` | 10 | 硬限制 |
| `min_improvement` | 0.01 | 低于此视为停滞 |
| `stagnation_patience` | 3 | 连续停滞轮数上限 |
| `regression_tolerance` | 0.05 | 回归容忍度，超出回滚 |
| `enable_ab_test` | true | A/B 分流验证 |

停止原因：`target_reached` · `stagnation` · `regression_with_stagnation` · `max_iterations_reached` · `no_failures_to_optimize` · `no_strategy_changes`。

### 治理与调度

| 模块 | 职责 |
|------|------|
| `governance/` | 用例去重、生命周期管理、校验器、审计日志 |
| `routing/` | 订阅者 + 匹配器 + 引擎，数据集路由到对应评估方法 |
| `scheduler/` | 定时任务调度、事件驱动 poller、服务管理 |
| `auth/` | JWT + bcrypt，多项目隔离 |

---

## 快速开始

### 1. 安装

```bash
pip install -e .
pip install -e ".[dev]"      # 开发依赖
pip install -e ".[bench]"    # BFCL-v4 / evalscope 支持
```

### 2. 配置

复制 `eval_config.example.yaml` 为 `eval_config.yaml`，并在 `.env` 中配置：

```ini
DB_HOST=localhost
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=postgres
DB_NAME=agent_eval

LLM_PROVIDER=openai
LLM_MODEL=claude-opus-4-7
LLM_JUDGE_MODEL=claude-opus-4-7
LLM_BASE_URL=https://your-endpoint/v1
LLM_API_KEY=sk-...

LANGSMITH_API_KEY=lsv2_...
LANGSMITH_API_URL=https://api.smith.langchain.com
```

### 3. 初始化数据库

```bash
alembic upgrade head
agent-eval init-db
```

### 4. 启动后端 + 前端

```bash
agent-eval server --host 0.0.0.0 --port 8000
cd frontend && npm install && npm run dev
```

---

## 典型用法

### 数据集管理

```bash
agent-eval dataset create my-eval-set --desc "初始评测集"
agent-eval dataset add-case my-eval-set --from-file cases.json
agent-eval dataset import-traces my-eval-set --project prod-agent --status success --limit 100
agent-eval dataset list --filter eval
agent-eval dataset show my-eval-set --tag failure --limit 20
agent-eval dataset stats my-eval-set
agent-eval dataset versions my-eval-set
agent-eval dataset export my-eval-set -o ./backup.jsonl --format jsonl
```

### LLM 用例生成

```bash
agent-eval dataset generate scenario my-eval-set \
    --scenario "用户问天气并要求写一封邮件" \
    --count 5 --tag weather --tag email

agent-eval dataset generate mutate my-eval-set \
    --case-id 3f2a --count 3 --strategy adversarial
```

### 评估

```bash
# 基于 Python Factory
agent-eval evaluate my-eval-set \
    --agent-module myapp.agent:factory \
    --concurrency 5

# 基于 HTTP API
agent-eval evaluate my-eval-set \
    --api-url https://my-agent/v1 \
    --api-type openai \
    --api-key sk-... \
    --api-model gpt-4o

# 基于 YAML 配置
agent-eval evaluate my-eval-set --config eval_config.yaml
```

### 闭环优化

```bash
agent-eval run my-eval-set \
    --agent-module myapp.agent:factory \
    --target-score 0.85 \
    --max-iterations 10 \
    --concurrency 5
```

### 标准 Benchmark

```bash
agent-eval bench bfcl \
    --api-url https://api.openai.com/v1 \
    --api-key sk-... \
    --model gpt-4o \
    --categories simple,multi_turn \
    --concurrency 5
```

---

## 项目结构

```
agent_eval/
├── pyproject.toml               项目配置与 CLI 入口
├── alembic/                     数据库迁移
├── eval_config.example.yaml     HTTP Agent 评测配置样例
├── bfcl_config.example.yaml     BFCL-v4 Benchmark 配置样例
│
├── src/agent_eval/
│   ├── cli.py                   Typer CLI 入口（dataset / bench / run / evaluate / server）
│   ├── config.py                Pydantic Settings 全局配置
│   ├── db.py                    SQLAlchemy async engine + session
│   │
│   ├── api/                     FastAPI 后端
│   │   ├── app.py               应用装配
│   │   ├── dependencies.py      依赖注入
│   │   ├── schemas.py           请求/响应模型
│   │   └── routers/             auth · projects · datasets · cases · traces
│   │                            benchmark · candidates · generate · routing
│   │                            scheduler · governance · config
│   │
│   ├── auth/                    JWT + bcrypt + 多项目隔离
│   ├── models/                  Pydantic 数据模型（TestCase · Trace · Score · Optimization）
│   ├── db_models/               SQLAlchemy ORM + Repository
│   │
│   ├── data/                    数据层
│   │   ├── dataset_manager.py   数据集版本与用例加载
│   │   ├── langsmith_provider.py LangSmith 适配
│   │   ├── case_generator.py    LLM 驱动用例生成
│   │   ├── trace_extractor.py   从线上 trace 回溯用例
│   │   ├── benchmark_import.py  Excel / CSV 导入
│   │   └── converter.py         格式转换
│   │
│   ├── evaluation/              评估层
│   │   ├── orchestrator.py      并发执行 + 评分编排
│   │   ├── trace_collector.py   LangChain Callback
│   │   ├── agent_adapter.py     OpenAI / SSE HTTP 适配器
│   │   ├── bench_config.py      YAML 配置
│   │   ├── bfcl_runner.py       BFCL-v4 集成
│   │   ├── regression.py        回归检测
│   │   ├── report.py            报告生成
│   │   └── scorers/             output · tool_sequence · reasoning
│   │                            performance · error_recovery · llm_judge
│   │
│   ├── optimization/            优化层
│   │   ├── failure_analyzer.py  LLM 分类 + 聚类
│   │   ├── strategy_generator.py LLM 策略生成
│   │   └── strategy_applicator.py 策略应用
│   │
│   ├── loop/                    闭环控制器
│   │   └── controller.py        收敛 / 停滞 / 回归三重安全阀
│   │
│   ├── governance/              治理
│   │   ├── dedup.py · lifecycle.py · validator.py · audit.py
│   │
│   ├── routing/                 数据集路由
│   │   ├── engine.py · matcher.py · subscriber.py
│   │
│   └── scheduler/               调度
│       ├── service.py · poller.py · events.py
│
├── frontend/                    React 18 + Vite + TypeScript + Tailwind
│   └── src/
│       ├── pages/               Login · Register · Projects · Dashboard
│       │                        Datasets · DatasetDetail · Traces
│       │                        Benchmark · Candidates · Generate
│       │                        AutoCollect · Routing · Scheduler
│       │                        Audit · Config
│       ├── components/Layout.tsx
│       ├── services/            Axios + React Query
│       ├── stores/              Zustand
│       └── types/
│
├── grafana/dashboards/          Grafana Dashboard JSON
├── prototype/                   原型草稿
└── tests/                       单测 + 集成测试
    ├── test_api/ · test_data/ · test_scorers/
    ├── test_optimization/ · test_loop/ · test_governance/
```

---

## Web UI

前端基于 React 18 + TypeScript + Vite + TailwindCSS + Zustand + React Query + Recharts。

| 页面 | 功能 |
|------|------|
| Projects | 多项目管理 |
| Dashboard | 评估结果概览、五维趋势、通过率 |
| Datasets / DatasetDetail | 数据集 CRUD、用例浏览 / 筛选 / 编辑 |
| Traces | 线上 trace 浏览与回溯导入 |
| Benchmark | BFCL 及自定义 Benchmark 执行 |
| Generate | LLM 驱动的用例生成界面 |
| Candidates | 候选用例审核与入库 |
| AutoCollect | 线上 trace 自动采集 |
| Routing | 数据集 → 评估方法路由配置 |
| Scheduler | 定时评估任务 |
| Audit | 操作审计日志 |
| Config | 系统配置 |

---

## Grafana 可视化

| 面板 | 类型 | 数据来源 |
|------|------|---------|
| 闭环总览 | 折线图 | `loop_control_log.aggregate_score by iteration` |
| 五维评分趋势 | 多线折线图 | `evaluation_scores` 按 dimension 聚合 |
| 失败模式分布 | 堆叠柱状图 | `optimizations.failure_analysis` JSONB 解析 |
| 用例通过率热力图 | 热力图 | `test_results × test_runs` 交叉 |
| 性能趋势 | 多线折线图 | `test_results.latency_ms / total_tokens` |
| 优化策略效果 | 柱状图 | `optimizations.improvement_delta` |
| 回归告警 | 表格 | `loop_control_log WHERE safety_stopped = true` |

数据源直连 PostgreSQL，利用 JSONB 字段的 `->>`、`jsonb_array_elements` 做深度查询。

---

## Agent Factory 接口

接入闭环需实现以下协议：

```python
from typing import Any, Protocol

class AgentFactory(Protocol):
    def get_config(self) -> dict[str, Any]:
        """返回当前 Agent 配置（system_prompt, tools, temperature 等）"""
        ...

    def create(self, config: dict[str, Any]) -> AgentProtocol:
        """根据配置创建 Agent 实例"""
        ...
```

策略应用器会深拷贝 `get_config()` 的返回值并修改，随后通过 `create()` 实例化新版本 Agent 进行对照评估——**不会**原地修改用户的 Agent。

---

## 数据模型

### 7 张核心表

| 表 | 职责 | 关键字段 |
|---|------|---------|
| `dataset_versions` | 数据集版本 | `version_tag` (UNIQUE), `parent_version` |
| `test_cases` | 用例存储 | `input_messages` / `expected_tool_calls` (JSONB), `tags` (ARRAY + GIN) |
| `test_runs` | 批次记录 | `agent_config` 快照 (JSONB), `ab_group`, `optimization_id` |
| `test_results` | 执行结果 | `actual_output`, `actual_tool_calls`, `full_trace` (JSONB) |
| `evaluation_scores` | 评分明细 | `dimension`, `score`, `weight`, `weighted_score`, `details` (JSONB) |
| `optimizations` | 优化记录 | `failure_analysis`, `strategy_detail`, `improvement_delta` (JSONB) |
| `loop_control_log` | 闭环日志 | `loop_session_id`, `iteration`, `aggregate_score`, `converged`, `safety_stopped` |

关系：

```
dataset_versions ←── test_cases
                 ←── test_runs ←── test_results ←── evaluation_scores
                                ←── optimizations
                                ←── loop_control_log
```

---

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `DB_HOST` · `DB_PORT` · `DB_USER` · `DB_PASSWORD` · `DB_NAME` | localhost:5432 / postgres | PostgreSQL |
| `LLM_PROVIDER` | openai | LLM 提供商 |
| `LLM_MODEL` | gpt-4o | Agent 默认模型 |
| `LLM_JUDGE_MODEL` | gpt-4o | Judge 模型 |
| `LLM_BASE_URL` | — | 自定义 OpenAI 兼容端点 |
| `LLM_API_KEY` | — | API Key |
| `LANGSMITH_API_KEY` / `LANGSMITH_API_URL` | — | LangSmith 托管 |
| `EVAL_BATCH_CONCURRENCY` | 5 | 评估并发 |
| `EVAL_FAILURE_THRESHOLD` | 0.7 | 失败判定阈值 |
| `LOOP_TARGET_SCORE` | 0.85 | 收敛目标 |
| `LOOP_MAX_ITERATIONS` | 10 | 最大轮数 |

---

## 实现状态

| 阶段 | 状态 |
|------|------|
| 数据层 — DatasetManager · LangSmithProvider · CaseGenerator · TraceExtractor · BenchmarkImport | 完成 |
| 评估层 — TraceCollector · 五维评分 · HTTP Adapter · BFCL-v4 · RegressionDetector | 完成 |
| 优化层 — FailureAnalyzer · StrategyGenerator · StrategyApplicator | 完成 |
| 闭环控制器 — LoopController + 三重安全阀 | 完成 |
| API 层 — FastAPI + 13 个 router + JWT Auth | 完成 |
| Web UI — React + 14 个页面 | 完成 |
| 治理 — 去重 / 生命周期 / 校验 / 审计 | 完成 |
| 路由 — 数据集 → 评估方法 | 完成 |
| 调度 — 定时任务 + 事件驱动 | 完成 |
| Grafana Dashboard | 进行中 |
| 端到端示例 Agent | 进行中 |

---

## 许可

内部项目，详见仓库根目录声明。
