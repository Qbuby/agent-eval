"""从参考答案提炼「答案关键点」的可复用服务。

原本这套逻辑只活在 ``scripts/extract_key_points.py`` 里（一次性全量回填的
CLI）。这里把它抽成 service 模块，让两个调用方共用同一份提炼语义：

- CLI（``scripts/extract_key_points.py``）：全量幂等回填，tenant 上下文为
  None，覆盖所有租户的行。
- HTTP 路由（数据集页面的「提炼关键点」按钮）：按当前租户 + 选中样例范围触发
  内存态异步 job，前端轮询进度。

背景
----
LLM Judge 的模板已打通 ``{{Criteria}}`` -> ``reference_criteria``（见评估器
固化参考要点改造）。但库里的样例大多只有「期望答案」没有「关键点」，judge
拿不到可逐条核对的清单，只能整段比对答案。本模块用 LLM 把长答案压成
2-8 条可核验的关键点，回填到各自的字段。

三类目标（字段位置不同，故分开处理）
    candidate : candidate_cases.key_points      <- answer
    benchmark : benchmark_cases.key_points      <- reference_answer
    multichat : Langfuse item 的
                expected_output.turn_expectations[].criteria
                                                <- 该轮 expected_output

幂等性
------
只挑选「有答案且关键点为空」的行，重跑不会覆盖已有关键点，也不会重复计费。
中断/取消后直接重跑即可续上。job 进度只驻内存（这是低频运维动作，重跑幂等，
无需为它建表落库）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import select, text

from agent_eval.data.langfuse_provider import (
    LangfuseDatasetProvider,
    build_langfuse_client,
)
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import (
    BenchmarkCaseRow,
    CandidateCaseRow,
    EvaluatorProviderRow,
)
from agent_eval.db_models.tenant_context import (
    TenantContext,
    reset_tenant_context,
    set_tenant_context,
)
from agent_eval.evaluation.judge_clients import JudgeClientError, build_judge_client
from agent_eval.models.test_case import TurnExpectation

logger = logging.getLogger(__name__)

# 提炼用的 provider。库里三条 provider：kiro(custom/claude-sonnet-5)、
# mimo(openai_compatible)、target-agent-sse(agent)。agent 类型是被测对象不能
# 拿来提炼；默认走 kiro。
DEFAULT_PROVIDER_NAME = "kiro"

# 单条提炼的输出上限。关键点是短句清单，1024 足够；给长答案留余量取 1500。
MAX_TOKENS = 1500
# 并发。judge_clients 自带 5 次指数退避重试，DNS/连接抖动能自愈；并发过高会
# 触发上游限流反而更慢，8 是实测折中。
DEFAULT_CONCURRENCY = 8

# 关键点条数区间。少于 2 条说明答案太短没必要拆，多于 8 条 judge 逐条核对
# 会失焦。
MIN_POINTS = 2
MAX_POINTS = 8

# 答案短于此长度不提炼（提炼出来的关键点会和答案本身等长，没有信息增益）。
MIN_ANSWER_CHARS = 20

# 三类目标标识。all 展开为按此顺序处理。
TARGETS = ("candidate", "benchmark", "multichat")

# 多轮对话集：样例真身在 Langfuse，没有 Postgres 表。
MULTICHAT_DATASETS = ("noble-agent-multichat", "ep-agent-multichat")

SYSTEM_PROMPT = """你是评测数据工程师。你的任务是把一份「参考答案」压缩成一组可逐条核对的**答案关键点**，供 LLM 评委据此判断被测 AI 的回答是否正确、是否有遗漏。

要求：
1. 每条关键点必须是**可客观核对**的具体事实、数值、步骤或结论，评委看一眼就能判断「回答里有没有、对不对」。
2. 只提炼参考答案中**实际存在**的信息，绝不补充、推测、润色任何答案里没有的内容。
3. 保留关键的数值、型号、单位、专有名词原样（如 2000 kg、EFL203P、VCM）。
4. 剔除排版噪音：markdown 标题符号、表格线、图片链接、引用标记（如 [citation:xxx]）、客套过渡语（如「以下是…」「希望对您有帮助」）都不要进关键点。
5. 若参考答案分步骤，按步骤顺序输出；若是参数罗列，每个关键参数一条。
6. 输出 %d 到 %d 条，由答案信息量决定，不要为凑数拆分或合并。
7. 用参考答案本身的语言书写（中文答案输出中文，德语答案输出德语）。

只输出一个 JSON 数组，元素为字符串，不要任何解释、不要 markdown 代码块。
形如：["关键点一", "关键点二", "关键点三"]""" % (MIN_POINTS, MAX_POINTS)

USER_TEMPLATE = """【用户问题】
{question}

【参考答案】
{answer}

请输出该参考答案的关键点 JSON 数组。"""


# ────────────────────────────────────────────────────────────────────────
# LLM 调用 + 解析
# ────────────────────────────────────────────────────────────────────────


def parse_points(raw: str) -> list[str]:
    """把模型回复解析成关键点列表。

    模型偶尔会裹 ```json 代码块或在数组前后加一句话，这里做容错：先剥代码
    块，再截取第一个 ``[`` 到最后一个 ``]``。解析失败或结构不对则返回空列表，
    由调用方计入失败、不写库（宁缺勿错——写错的关键点会污染后续所有评估）。
    """
    s = (raw or "").strip()
    if not s:
        return []
    # 剥 ```json ... ``` 围栏
    fence = re.match(r"^```[a-zA-Z]*\s*(.*?)\s*```$", s, re.DOTALL)
    if fence:
        s = fence.group(1).strip()
    # 截取数组主体，容忍前后多余文字
    start, end = s.find("["), s.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        data = json.loads(s[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    points: list[str] = []
    for item in data:
        # 只接受字符串元素；模型偶发返回 {"point": "..."} 时取其值。
        if isinstance(item, str):
            txt = item.strip()
        elif isinstance(item, dict):
            vals = [v for v in item.values() if isinstance(v, str) and v.strip()]
            txt = vals[0].strip() if vals else ""
        else:
            txt = ""
        if txt:
            points.append(txt)
    return points


async def extract_one(
    client: Any,
    sem: asyncio.Semaphore,
    question: str,
    answer: str,
    *,
    cancel_event: asyncio.Event | None = None,
) -> tuple[list[str], str | None]:
    """提炼单条。返回 (关键点, 错误信息)；成功时错误为 None。

    ``cancel_event`` 用于批量任务的取消：已排队但还没抢到 sem 的单元在抢到闸门
    后立即放弃，不再多花一次 LLM 调用。
    """
    async with sem:
        if cancel_event is not None and cancel_event.is_set():
            return [], "已取消"
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": USER_TEMPLATE.format(question=question or "(无)", answer=answer),
            },
        ]
        try:
            result = await client.ainvoke(messages)
        except JudgeClientError as e:
            return [], str(e)
        except Exception as e:  # noqa: BLE001 - 单条失败不该中断全量
            return [], f"{type(e).__name__}: {e}"

        points = parse_points(result.content)
        if not points:
            preview = (result.content or "")[:160].replace("\n", " ")
            return [], f"无法解析出关键点，模型回复: {preview}"
        return points, None


# ────────────────────────────────────────────────────────────────────────
# 待提炼单元
# ────────────────────────────────────────────────────────────────────────


@dataclass
class Unit:
    """一个待提炼单元。

    ``ref`` 用于回写定位：candidate/benchmark 是行 id；multichat 是
    (item_id, turn_index)。
    """

    kind: str
    ref: Any
    question: str
    answer: str
    label: str
    points: list[str] = field(default_factory=list)
    error: str | None = None


def too_short(answer: str) -> bool:
    return len(answer.strip()) < MIN_ANSWER_CHARS


# ────────────────────────────────────────────────────────────────────────
# 采集：三类数据源 → Unit 列表
# ────────────────────────────────────────────────────────────────────────


async def collect_candidate(
    limit: int | None = None, *, case_ids: list[str] | None = None
) -> list[Unit]:
    """candidate_cases：有 answer 且 key_points 空。

    key_points 是 ``list | None``，历史行大量为 SQL NULL（探查确认全是 null，
    没有标量畸形值），故用 jsonb 类型安全判定而非 jsonb_array_length。

    ``case_ids`` 非空时只处理这些行（路由按用户选中的样例范围触发）；租户过滤
    由 tenant 上下文经 SQLAlchemy 事件注入，这里不显式带。
    """
    units: list[Unit] = []
    async with async_session_factory() as session:
        stmt = (
            select(CandidateCaseRow)
            .where(
                text(
                    "COALESCE(TRIM(candidate_cases.answer), '') <> '' AND "
                    "(candidate_cases.key_points IS NULL OR "
                    " jsonb_typeof(candidate_cases.key_points) <> 'array' OR "
                    " jsonb_array_length(candidate_cases.key_points) = 0)"
                )
            )
            .order_by(CandidateCaseRow.created_at)
        )
        if case_ids:
            stmt = stmt.where(CandidateCaseRow.id.in_(case_ids))
        if limit:
            stmt = stmt.limit(limit)
        rows = (await session.execute(stmt)).scalars().all()

    for r in rows:
        units.append(
            Unit(
                kind="candidate",
                ref=r.id,
                question=r.question or "",
                answer=r.answer or "",
                label=f"candidate[{str(r.id)[:8]}] {(r.question or '')[:30]}",
            )
        )
    return units


async def collect_benchmark(
    limit: int | None = None, *, case_ids: list[str] | None = None
) -> list[Unit]:
    """benchmark_cases：有 reference_answer 且 key_points 空。"""
    units: list[Unit] = []
    async with async_session_factory() as session:
        stmt = (
            select(BenchmarkCaseRow)
            .where(
                text(
                    "COALESCE(TRIM(benchmark_cases.reference_answer), '') <> '' AND "
                    "(benchmark_cases.key_points IS NULL OR "
                    " jsonb_typeof(benchmark_cases.key_points) <> 'array' OR "
                    " jsonb_array_length(benchmark_cases.key_points) = 0)"
                )
            )
            .order_by(BenchmarkCaseRow.created_at)
        )
        if case_ids:
            stmt = stmt.where(BenchmarkCaseRow.id.in_(case_ids))
        if limit:
            stmt = stmt.limit(limit)
        rows = (await session.execute(stmt)).scalars().all()

    for r in rows:
        units.append(
            Unit(
                kind="benchmark",
                ref=r.id,
                question=r.question or "",
                answer=r.reference_answer or "",
                label=f"benchmark[{str(r.id)[:8]}] {(r.question or '')[:30]}",
            )
        )
    return units


async def collect_multichat(
    provider: LangfuseDatasetProvider,
    limit: int | None = None,
    *,
    dataset_names: tuple[str, ...] | list[str] | None = None,
    item_ids: list[str] | None = None,
) -> tuple[list[Unit], dict[str, Any]]:
    """多轮对话集：逐轮 turn_expectations 里有 expected_output 但 criteria 空。

    同时返回 item_id -> TestCase 的映射，回写时要拿整个 case 走 update_case
    （Langfuse 的写接口是整 item upsert，没有字段级 patch）。

    ``dataset_names`` 覆盖默认的两个多轮集（路由针对单个数据集触发）；
    ``item_ids`` 非空时只处理这些 item（用户选中的样例范围）。
    """
    units: list[Unit] = []
    cases: dict[str, Any] = {}
    names = tuple(dataset_names) if dataset_names else MULTICHAT_DATASETS
    item_id_set = set(item_ids) if item_ids else None

    for ds_name in names:
        # load_cases 已在 provider 内部走 converter，返回 TestCase；
        # case.id 即 Langfuse 的 example_id，回写时原样传给 update_case。
        loaded = await provider.load_cases(ds_name)
        for case in loaded:
            if not case.turn_expectations:
                continue
            if item_id_set is not None and case.id not in item_id_set:
                continue
            cases[case.id] = case
            # 该轮的用户问题：turn_index 指向 input_messages 里的 user 消息。
            msgs = case.input_messages or []
            for te in case.turn_expectations:
                if te.criteria:
                    continue  # 已有关键点，跳过（幂等）
                expected = (te.expected_output or "").strip()
                if not expected:
                    continue  # 无期望答案，无从提炼
                ti = te.turn_index
                q = ""
                if 0 <= ti < len(msgs) and isinstance(msgs[ti], dict):
                    q = str(msgs[ti].get("content") or "")
                units.append(
                    Unit(
                        kind="multichat",
                        ref=(case.id, ti),
                        question=q,
                        answer=expected,
                        label=f"{ds_name}[{case.id[:8]}] turn{ti} {q[:24]}",
                    )
                )
                if limit and len(units) >= limit:
                    return units, cases
    return units, cases


# ────────────────────────────────────────────────────────────────────────
# 回写
# ────────────────────────────────────────────────────────────────────────


async def write_sql(units: list[Unit]) -> int:
    """回写 candidate_cases / benchmark_cases 的 key_points。

    这两张表都是 TenantMixin。UPDATE 现有行不经过 before_flush 的新对象补齐
    分支，tenant_id 原样保留；CLI 场景 tenant 上下文为 None（系统/后台）走旁路
    不过滤，因此能覆盖全部租户的行；路由场景 tenant 上下文已 set，只能命中本
    租户的行，天然隔离。
    """
    ok = 0
    async with async_session_factory() as session:
        for u in units:
            if not u.points:
                continue
            if u.kind == "candidate":
                row = await session.get(CandidateCaseRow, u.ref)
            else:
                row = await session.get(BenchmarkCaseRow, u.ref)
            if row is None:
                u.error = "回写时行已不存在"
                continue
            row.key_points = u.points
            ok += 1
        await session.commit()
    return ok


async def write_multichat(
    provider: LangfuseDatasetProvider,
    units: list[Unit],
    cases: dict[str, Any],
) -> int:
    """回写 Langfuse item 的 turn_expectations[].criteria。

    Langfuse 只能整 item upsert，故按 item 聚合该 item 下所有成功的轮，一次
    写回。converter 往返保真已实测（input/output/metadata 三栏无字段丢失），
    走 update_case 不会丢原有字段。
    """
    by_item: dict[str, list[Unit]] = {}
    for u in units:
        if not u.points:
            continue
        item_id, _ = u.ref
        by_item.setdefault(item_id, []).append(u)

    ok = 0
    for item_id, item_units in by_item.items():
        case = cases.get(item_id)
        if case is None:
            for u in item_units:
                u.error = "回写时找不到对应 case"
            continue
        points_by_turn = {u.ref[1]: u.points for u in item_units}
        new_tes: list[TurnExpectation] = []
        for te in case.turn_expectations:
            pts = points_by_turn.get(te.turn_index)
            if pts:
                te = te.model_copy(update={"criteria": pts})
            new_tes.append(te)
        case.turn_expectations = new_tes
        try:
            await provider.update_case(case.id, case)
        except Exception as e:  # noqa: BLE001
            for u in item_units:
                u.error = f"写回 Langfuse 失败: {type(e).__name__}: {e}"
            continue
        ok += len(item_units)
    return ok


# ────────────────────────────────────────────────────────────────────────
# provider 加载 + 批量提炼核心
# ────────────────────────────────────────────────────────────────────────


class ExtractionError(RuntimeError):
    """提炼流程的可预期错误（provider 找不到、类型不对等），供路由转 4xx。"""


async def load_provider_row(name: str) -> EvaluatorProviderRow:
    async with async_session_factory() as session:
        stmt = select(EvaluatorProviderRow).where(EvaluatorProviderRow.name == name)
        row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise ExtractionError(f"找不到 provider '{name}'")
    if row.provider_type == "agent":
        raise ExtractionError(
            f"provider '{name}' 是 agent(SSE) 类型——那是被测对象，不能用来提炼"
        )
    return row


async def extract_units(
    units: list[Unit],
    *,
    provider_row: EvaluatorProviderRow,
    model: str | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    cancel_event: asyncio.Event | None = None,
    on_progress: Callable[[int, int, Unit], None] | None = None,
) -> None:
    """并发提炼一批 Unit，结果原地写回各 Unit 的 points/error。

    只建一次 judge client，全批复用；进度通过 ``on_progress(done, total, unit)``
    回调外抛，供 CLI 打日志或 job 更新内存进度。
    """
    if not units:
        return
    sem = asyncio.Semaphore(max(1, concurrency))
    done = 0

    async def _run(u: Unit, client: Any) -> None:
        nonlocal done
        u.points, u.error = await extract_one(
            client, sem, u.question, u.answer, cancel_event=cancel_event
        )
        done += 1
        if on_progress is not None:
            on_progress(done, len(units), u)

    async with build_judge_client(
        provider_row, model=model, max_tokens=MAX_TOKENS, timeout=120.0
    ) as client:
        await asyncio.gather(*[_run(u, client) for u in units])


@dataclass
class ExtractionResult:
    """一次提炼流程的汇总（采集→提炼→回写全走完后返回）。"""

    units: list[Unit]
    pending: int = 0
    skipped_short: int = 0
    extracted: int = 0
    failed: int = 0
    written: int = 0
    write_failed: int = 0


async def run_extraction(
    *,
    targets: list[str],
    limit: int | None = None,
    provider_name: str = DEFAULT_PROVIDER_NAME,
    model: str | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    case_ids: list[str] | None = None,
    dataset_names: tuple[str, ...] | list[str] | None = None,
    dry_run: bool = False,
    cancel_event: asyncio.Event | None = None,
    on_progress: Callable[[int, int, Unit], None] | None = None,
    on_phase: Callable[[str], None] | None = None,
) -> ExtractionResult:
    """采集 → 提炼 → 回写的端到端编排。CLI 与路由 job 都调它。

    ``on_phase(phase)`` 在阶段切换时回调（collecting/extracting/writing）；
    ``dry_run`` 只提炼不写库。取消在采集/提炼两个阶段都会尽早生效。
    """
    def _phase(p: str) -> None:
        if on_phase is not None:
            on_phase(p)

    lf_provider: LangfuseDatasetProvider | None = None
    cases: dict[str, Any] = {}
    units: list[Unit] = []
    skipped_short = 0

    _phase("collecting")
    for t in targets:
        if cancel_event is not None and cancel_event.is_set():
            break
        if t == "candidate":
            got = await collect_candidate(limit, case_ids=case_ids)
        elif t == "benchmark":
            got = await collect_benchmark(limit, case_ids=case_ids)
        elif t == "multichat":
            lf_provider = LangfuseDatasetProvider(await build_langfuse_client())
            got, cases = await collect_multichat(
                lf_provider, limit, dataset_names=dataset_names, item_ids=case_ids
            )
        else:
            raise ExtractionError(f"未知 target: {t!r}")
        # 过短答案不值得提炼，单独计数而非静默丢弃。
        keep = [u for u in got if not too_short(u.answer)]
        skipped_short += len(got) - len(keep)
        units.extend(keep)
        logger.info("采集 %s: 待提炼 %d 条", t, len(keep))

    result = ExtractionResult(units=units, pending=len(units), skipped_short=skipped_short)
    if not units:
        _phase("done")
        return result

    provider_row = await load_provider_row(provider_name)

    _phase("extracting")
    await extract_units(
        units,
        provider_row=provider_row,
        model=model,
        concurrency=concurrency,
        cancel_event=cancel_event,
        on_progress=on_progress,
    )

    ok_units = [u for u in units if u.points]
    result.extracted = len(ok_units)
    result.failed = len([u for u in units if not u.points])

    if dry_run:
        _phase("done")
        return result

    _phase("writing")
    sql_units = [u for u in ok_units if u.kind in ("candidate", "benchmark")]
    mc_units = [u for u in ok_units if u.kind == "multichat"]

    written = 0
    if sql_units:
        written += await write_sql(sql_units)
    if mc_units and lf_provider is not None:
        written += await write_multichat(lf_provider, mc_units, cases)
    result.written = written
    result.write_failed = len([u for u in ok_units if u.error])
    _phase("done")
    return result


# ────────────────────────────────────────────────────────────────────────
# 内存态异步 job（供 HTTP 路由触发 + 轮询）
# ────────────────────────────────────────────────────────────────────────


@dataclass
class _ExtractionHandle:
    job_id: str
    task: asyncio.Task | None
    cancel_event: asyncio.Event
    status: dict[str, Any] = field(
        default_factory=lambda: {
            "phase": "pending",  # pending→collecting→extracting→writing→done/failed/cancelled
            "total": 0,
            "done": 0,
            "extracted": 0,
            "failed": 0,
            "written": 0,
            "skipped_short": 0,
            "error": None,
            "targets": [],
        }
    )


# 提炼是低频运维动作，完成的 handle 也留在注册表里供前端拉到终态；不像
# reply_generator 有 DB 兜底，这里 pop 掉就再也查不到结果了。
_JOB_REGISTRY: dict[str, _ExtractionHandle] = {}


def get_job_status(job_id: str) -> dict[str, Any] | None:
    """job 状态快照。未知 job 返回 None（路由据此转 404）。"""
    h = _JOB_REGISTRY.get(job_id)
    return dict(h.status) if h else None


def request_cancel(job_id: str) -> bool:
    """请求取消。正在跑的那条 LLM 调用跑完为止（不硬杀），后续单元直接放弃。
    返回 False 表示本进程没有这个 job。"""
    h = _JOB_REGISTRY.get(job_id)
    if h is None:
        return False
    h.cancel_event.set()
    return True


def is_job_active(job_id: str) -> bool:
    h = _JOB_REGISTRY.get(job_id)
    return bool(h and h.task is not None and not h.task.done())


async def start_extraction_job(
    *,
    targets: list[str],
    limit: int | None = None,
    provider_name: str = DEFAULT_PROVIDER_NAME,
    model: str | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    case_ids: list[str] | None = None,
    dataset_names: tuple[str, ...] | list[str] | None = None,
    tenant_ctx: TenantContext | None = None,
) -> str:
    """建内存态 job 并后台跑，返回 job_id。

    ``tenant_ctx`` 由调用方（HTTP 层）从请求上下文取，后台任务里重新 set 一次
    ——``asyncio.create_task`` 不继承 contextvar 的后续修改，不显式带会导致采集
    /回写落到内部 sentinel 租户。CLI 全量回填传 None（旁路不过滤）。
    """
    bad = [t for t in targets if t not in TARGETS]
    if bad:
        raise ExtractionError(f"未知 target: {bad!r}")
    if not targets:
        raise ExtractionError("targets 为空")

    job_id = uuid.uuid4().hex
    cancel_event = asyncio.Event()
    handle = _ExtractionHandle(job_id=job_id, task=None, cancel_event=cancel_event)
    handle.status["targets"] = list(targets)
    handle.task = asyncio.create_task(
        _execute_job(
            job_id=job_id,
            targets=targets,
            limit=limit,
            provider_name=provider_name,
            model=model,
            concurrency=concurrency,
            case_ids=case_ids,
            dataset_names=dataset_names,
            cancel_event=cancel_event,
            handle=handle,
            tenant_ctx=tenant_ctx,
        )
    )
    _JOB_REGISTRY[job_id] = handle
    return job_id


async def _execute_job(
    *,
    job_id: str,
    targets: list[str],
    limit: int | None,
    provider_name: str,
    model: str | None,
    concurrency: int,
    case_ids: list[str] | None,
    dataset_names: tuple[str, ...] | list[str] | None,
    cancel_event: asyncio.Event,
    handle: _ExtractionHandle,
    tenant_ctx: TenantContext | None,
) -> None:
    """后台任务体：跑 run_extraction，把阶段/进度实时写进 handle.status。"""
    ctx_token = None
    if tenant_ctx is not None:
        ctx_token = set_tenant_context(tenant_ctx)
    st = handle.status

    def _on_phase(phase: str) -> None:
        # done 由 finally 统一按取消/失败改写，这里只透传中间阶段。
        if phase != "done":
            st["phase"] = phase

    def _on_progress(done: int, total: int, unit: Unit) -> None:
        st["total"] = total
        st["done"] = done

    try:
        result = await run_extraction(
            targets=targets,
            limit=limit,
            provider_name=provider_name,
            model=model,
            concurrency=concurrency,
            case_ids=case_ids,
            dataset_names=dataset_names,
            cancel_event=cancel_event,
            on_progress=_on_progress,
            on_phase=_on_phase,
        )
        st["total"] = result.pending
        st["extracted"] = result.extracted
        st["failed"] = result.failed
        st["written"] = result.written
        st["skipped_short"] = result.skipped_short
        st["phase"] = "cancelled" if cancel_event.is_set() else "done"
    except ExtractionError as e:
        st["error"] = str(e)
        st["phase"] = "failed"
    except Exception as e:  # noqa: BLE001
        logger.exception("extraction job %s failed: %s", job_id, e)
        st["error"] = f"{type(e).__name__}: {e}"
        st["phase"] = "failed"
    finally:
        if ctx_token is not None:
            reset_tenant_context(ctx_token)
