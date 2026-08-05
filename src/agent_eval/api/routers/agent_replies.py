"""持久化 agent 回复的 HTTP API。

三类数据集（备选 / 基准 / 多轮对话）的样例都能在这里「让 agent 生成答案」，
生成结果落成版本行，支持版本回溯、编辑、设为当前、删除，并可作为评估的数据
来源（评估时不再实时调 agent）。

权限：router 级 ``require_internal()`` —— 外部客户（external_customer）拿 403。
写操作（生成 / 编辑 / 设当前 / 删除）额外要求 ``ROLE_ADMIN | ROLE_USER``，即与
router 级门禁同域；操作者写进版本行的 ``created_by``。

样例引用统一是 ``(dataset_type, case_ref)``：
  candidate / benchmark → 本地表主键的字符串形式
  conversation          → Langfuse dataset item id
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from agent_eval.api.schemas import EvalAgentConfig
from agent_eval.auth.dependencies import require_internal
from agent_eval.data.content_blocks import merge_question_content
from agent_eval.db import async_session_factory
from agent_eval.db_models.repository import Repository
from agent_eval.db_models.tables import (
    BenchmarkCaseRow,
    CandidateCaseRow,
    UserRow,
)
from agent_eval.db_models.tenant_context import get_tenant_context
from agent_eval.evaluation import reply_generator

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/agent-replies",
    tags=["agent-replies"],
    dependencies=[Depends(require_internal())],
)

_DATASET_TYPES = set(reply_generator.DATASET_TYPES)


# ───────────────────────────────────────────────────────────────────────────
# Schemas
# ───────────────────────────────────────────────────────────────────────────


class GenerateRepliesRequest(BaseModel):
    """「agent生成答案」表单提交体。

    ``case_ids`` 是勾选的样例（支持多选 / 当前页全选，不做跨页全量——前端只把
    可见勾选项发上来）。``agent`` 复用评估任务的 ``EvalAgentConfig`` 协议，故
    前端可以直接复用配置组件。``version_label`` 是用户自定义版本号：agent 配置
    的差异由它体现，服务端不额外按配置划分版本链。
    """

    dataset_type: str
    dataset_name: str | None = None
    project_id: str | None = None
    case_ids: list[str] = Field(min_length=1)
    agent: EvalAgentConfig
    version_label: str | None = None
    concurrency: int = Field(default=reply_generator.DEFAULT_CONCURRENCY, ge=1, le=20)


class ReplyVersionOut(BaseModel):
    id: str
    dataset_type: str
    case_ref: str
    version_number: int
    version_label: str | None = None
    model: str | None = None
    content: str | None = None
    turns: list[Any] | None = None
    status: str
    error_message: str | None = None
    latency_ms: int | None = None
    total_tokens: int | None = None
    edited: bool = False
    is_current: bool = False
    agent_config: dict[str, Any] = {}
    created_by: str | None = None
    created_by_name: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    used_by_results: int = 0


class UpdateVersionRequest(BaseModel):
    content: str | None = None
    version_label: str | None = None
    turns: list[Any] | None = None


class CaseReplyStateOut(BaseModel):
    """列表页每行的回复状态摘要。"""

    case_ref: str
    has_reply: bool
    current_version_id: str | None = None
    current_version_number: int | None = None
    current_version_label: str | None = None
    current_model: str | None = None
    version_count: int = 0


class BatchVersionSelector(BaseModel):
    """批量切换版本的「按什么挑」。

    version_id 是 per-(dataset_type, case_ref) 的，跨样例不可复用，所以批量切换
    只能按跨样例可识别的标识来指定：

    - ``latest``          每个样例各自版本链里最新的成功版本
    - ``version_number``  精确 vN（各样例的 vN 是不同的版本行）
    - ``label``           版本备注完全相同的那条；同一备注多条时取最新
    - ``model``           agent_config.model 相同的那条；同一模型多条时取最新
    """

    mode: str = "latest"
    version_number: int | None = None
    label: str | None = None
    model: str | None = None


class BatchSetCurrentRequest(BaseModel):
    dataset_type: str
    case_refs: list[str] = Field(min_length=1, max_length=2000)
    selector: BatchVersionSelector = BatchVersionSelector()


class BatchResolveItemOut(BaseModel):
    """单个样例的解析结果。matched=False 时 reason 说明为什么切不了。"""

    case_ref: str
    matched: bool
    already_current: bool = False
    version_id: str | None = None
    version_number: int | None = None
    version_label: str | None = None
    model: str | None = None
    status: str | None = None
    reason: str | None = None


class BatchOptionOut(BaseModel):
    """可选的批量标识及命中样例数（前端下拉直接用）。"""

    value: str
    case_count: int


class BatchResolveOut(BaseModel):
    total: int
    matched_count: int
    changed_count: int
    unchanged_count: int
    missing_count: int
    items: list[BatchResolveItemOut]
    label_options: list[BatchOptionOut] = []
    version_number_options: list[BatchOptionOut] = []
    model_options: list[BatchOptionOut] = []


class BatchSetCurrentOut(BaseModel):
    total: int
    changed_count: int
    unchanged_count: int
    missing_count: int
    failed_count: int
    items: list[BatchResolveItemOut]


class JobItemOut(BaseModel):
    id: str
    case_ref: str
    question: str | None = None
    status: str
    error_message: str | None = None
    version_id: str | None = None


class JobOut(BaseModel):
    id: str
    dataset_type: str
    dataset_name: str | None = None
    status: str
    version_label: str | None = None
    model: str | None = None
    total_count: int
    succeeded_count: int
    failed_count: int
    running_count: int
    cancel_requested: bool = False
    created_at: str | None = None
    finished_at: str | None = None
    created_by: str | None = None
    created_by_name: str | None = None
    items: list[JobItemOut] = []


# ───────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────


def _iso(dt: Any) -> str | None:
    return dt.isoformat() if dt is not None else None


def _check_dataset_type(dataset_type: str) -> str:
    if dataset_type not in _DATASET_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"dataset_type 必须是 {sorted(_DATASET_TYPES)} 之一",
        )
    return dataset_type


async def _load_user_names(repo: Repository, ids: set[uuid.UUID]) -> dict[str, str]:
    """批量取操作者用户名（仅内部用户可见操作员，router 级门禁已保证）。"""
    if not ids:
        return {}
    rows = (await repo.session.execute(
        select(UserRow).where(UserRow.id.in_(list(ids)))
    )).scalars().all()
    return {str(r.id): (r.username or r.email or str(r.id)) for r in rows}


async def _resolve_cases(
    *,
    dataset_type: str,
    case_ids: list[str],
    dataset_name: str | None,
    session,
) -> list[dict[str, Any]]:
    """把勾选的样例 id 解析成生成引擎要的 case dict。

    - candidate / benchmark：本地表按主键取，用户问题作 agent 输入（单轮）。
    - conversation：样例真身在 Langfuse，全量 load 后按 id 内存筛；带
      ``multi_turn=True`` + ``input_messages``，生成引擎逐轮调用并补全所有
      assistant 回复，每轮携带此前完整上下文。
    """
    cases: list[dict[str, Any]] = []

    if dataset_type == "candidate":
        try:
            uuids = [uuid.UUID(x) for x in case_ids]
        except (TypeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=f"非法样例 id：{e}") from e
        rows = (await session.execute(
            select(CandidateCaseRow).where(CandidateCaseRow.id.in_(uuids))
        )).scalars().all()
        for r in rows:
            cases.append({
                "id": str(r.id),
                # 带附件样例送 blocks（生成引擎与 adapter 原样透传），纯文本样例
                # 回落字符串，与改造前逐字节一致。
                "question": merge_question_content(r.question, r.question_content),
                "multi_turn": False,
            })
    elif dataset_type == "benchmark":
        try:
            uuids = [uuid.UUID(x) for x in case_ids]
        except (TypeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=f"非法样例 id：{e}") from e
        rows = (await session.execute(
            select(BenchmarkCaseRow).where(BenchmarkCaseRow.id.in_(uuids))
        )).scalars().all()
        for r in rows:
            cases.append({
                "id": str(r.id),
                # 与 candidate 分支同理：带附件样例必须送 blocks，否则图片在这里
                # 丢掉，agent 只收到含 [图片] 占位的纯文本。
                "question": merge_question_content(r.question, r.question_content),
                "multi_turn": False,
            })
    else:
        if not dataset_name:
            raise HTTPException(
                status_code=400, detail="多轮对话集必须提供 dataset_name",
            )
        from agent_eval.api.dependencies import get_manager

        mgr = await get_manager()
        try:
            ds_cases = await mgr.load_cases(dataset_name, limit=None)
        except Exception as e:
            raise HTTPException(
                status_code=502, detail=f"读取对话数据集 '{dataset_name}' 失败：{e}",
            ) from e
        wanted = set(case_ids)
        for c in ds_cases:
            if c.id not in wanted:
                continue
            msgs = c.input_messages or []
            if not any(m.get("role") == "user" and m.get("content") for m in msgs):
                continue
            first_user = next(
                (m.get("content", "") for m in msgs if m.get("role") == "user"), ""
            )
            cases.append({
                "id": c.id,
                "question": first_user,
                "multi_turn": True,
                "input_messages": msgs,
            })

    if not cases:
        raise HTTPException(status_code=400, detail="选中的样例都不存在或没有问题内容")
    return cases


def _cfg_model(row: Any) -> str | None:
    """版本 / 任务的配置快照里的模型标识；取不到返回 None（UI 显示「未记录」）。"""
    cfg = getattr(row, "agent_config", None) or {}
    if not isinstance(cfg, dict):
        return None
    model = cfg.get("model")
    if not model:
        return None
    return str(model).strip() or None


def _default_version_label(label: str | None, agent_cfg: dict[str, Any]) -> str | None:
    """版本备注留空时回落到模型标识。

    备注是跨样例可识别的批量标识，留空会让整条版本链无从区分。用模型名兜底后，
    「按版本备注」和「按模型」两个维度对同一批版本都能对齐。
    """
    text = (label or "").strip()
    if not text:
        text = str(agent_cfg.get("model") or "").strip()
    return text[:64] or None


def _version_out(
    row: Any,
    *,
    is_current: bool = False,
    used_by_results: int = 0,
    user_names: dict[str, str] | None = None,
) -> ReplyVersionOut:
    names = user_names or {}
    created_by = str(row.created_by) if row.created_by else None
    return ReplyVersionOut(
        id=str(row.id),
        dataset_type=row.dataset_type,
        case_ref=row.case_ref,
        version_number=row.version_number,
        version_label=row.version_label,
        model=_cfg_model(row),
        content=row.content,
        turns=row.turns,
        status=row.status,
        error_message=row.error_message,
        latency_ms=row.latency_ms,
        total_tokens=row.total_tokens,
        edited=bool(row.edited),
        is_current=is_current,
        agent_config=row.agent_config or {},
        created_by=created_by,
        created_by_name=names.get(created_by or ""),
        created_at=_iso(row.created_at),
        updated_at=_iso(row.updated_at),
        used_by_results=used_by_results,
    )


def _job_out(job: Any, items: list[Any], user_names: dict[str, str]) -> JobOut:
    created_by = str(job.created_by) if job.created_by else None
    # 内存进度优先（刚点下去还没落库的瞬间），DB 计数兜底（刷新恢复）。
    mem = reply_generator.get_job_progress(str(job.id))
    return JobOut(
        id=str(job.id),
        dataset_type=job.dataset_type,
        dataset_name=job.dataset_name,
        status=job.status,
        version_label=job.version_label,
        model=_cfg_model(job),
        total_count=int(job.total_count or 0),
        succeeded_count=int(mem.get("succeeded", job.succeeded_count or 0)),
        failed_count=int(mem.get("failed", job.failed_count or 0)),
        running_count=int(mem.get("running", job.running_count or 0)),
        cancel_requested=bool(job.cancel_requested),
        created_at=_iso(job.created_at),
        finished_at=_iso(job.finished_at),
        created_by=created_by,
        created_by_name=user_names.get(created_by or ""),
        items=[
            JobItemOut(
                id=str(i.id),
                case_ref=i.case_ref,
                question=i.question,
                status=i.status,
                error_message=i.error_message,
                version_id=str(i.version_id) if i.version_id else None,
            )
            for i in items
        ],
    )


def _batch_options(
    grouped: dict[str, list[Any]],
) -> tuple[list[BatchOptionOut], list[BatchOptionOut], list[BatchOptionOut]]:
    """从一批样例的版本链里汇总出可用的批量标识及各自命中的样例数。

    只统计成功版本 —— 失败版本设不了当前，出现在下拉里只会误导用户。
    模型一栏取自各版本的配置快照，是「这批样例被哪些模型跑过」的真实来源，
    不依赖用户有没有手填版本备注。
    """
    label_cases: dict[str, set[str]] = {}
    number_cases: dict[int, set[str]] = {}
    model_cases: dict[str, set[str]] = {}
    for ref, versions in grouped.items():
        for v in versions:
            if v.status != "succeeded":
                continue
            if v.version_label:
                label_cases.setdefault(v.version_label, set()).add(ref)
            number_cases.setdefault(int(v.version_number), set()).add(ref)
            model = _cfg_model(v)
            if model:
                model_cases.setdefault(model, set()).add(ref)
    labels = [
        BatchOptionOut(value=k, case_count=len(v))
        for k, v in sorted(label_cases.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]
    numbers = [
        BatchOptionOut(value=f"v{k}", case_count=len(v))
        for k, v in sorted(number_cases.items(), reverse=True)
    ]
    models = [
        BatchOptionOut(value=k, case_count=len(v))
        for k, v in sorted(model_cases.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]
    return labels, numbers, models


async def _resolve_batch_versions(
    repo: Repository,
    *,
    dataset_type: str,
    case_refs: list[str],
    selector: BatchVersionSelector,
) -> BatchResolveOut:
    """把一个批量意图在每个样例各自的版本链里解析成一条具体版本。

    「预览」和「执行」共用这里，保证用户看到的和实际切的是同一套判定。
    """
    if selector.mode not in ("latest", "version_number", "label", "model"):
        raise HTTPException(
            status_code=400,
            detail="selector.mode 必须是 latest / version_number / label / model 之一",
        )
    if selector.mode == "version_number" and selector.version_number is None:
        raise HTTPException(
            status_code=400, detail="按版本号批量切换必须提供 version_number",
        )
    if selector.mode == "label" and not (selector.label or "").strip():
        raise HTTPException(status_code=400, detail="按版本备注批量切换必须提供 label")
    if selector.mode == "model" and not (selector.model or "").strip():
        raise HTTPException(status_code=400, detail="按模型批量切换必须提供 model")

    refs = list(dict.fromkeys(case_refs))
    grouped = await repo.list_agent_reply_versions_by_cases(dataset_type, refs)
    states = await repo.list_agent_reply_case_states(dataset_type, refs)
    current_by_ref = {
        s.case_ref: s.current_version_id for s in states if s.current_version_id
    }
    label = (selector.label or "").strip()
    model = (selector.model or "").strip()

    items: list[BatchResolveItemOut] = []
    for ref in refs:
        versions = grouped.get(ref) or []
        if not versions:
            items.append(BatchResolveItemOut(
                case_ref=ref, matched=False, reason="该样例还没有生成过回复",
            ))
            continue
        if selector.mode == "latest":
            hit = next((v for v in versions if v.status == "succeeded"), None)
            reason = None if hit is not None else "该样例的所有版本都是生成失败的"
        elif selector.mode == "version_number":
            same = [
                v for v in versions
                if int(v.version_number) == selector.version_number
            ]
            hit = next((v for v in same if v.status == "succeeded"), None)
            if hit is not None:
                reason = None
            elif same:
                reason = f"v{selector.version_number} 生成失败，不能设为当前版本"
            else:
                reason = f"该样例没有 v{selector.version_number}"
        elif selector.mode == "label":
            same = [v for v in versions if (v.version_label or "") == label]
            hit = next((v for v in same if v.status == "succeeded"), None)
            if hit is not None:
                reason = None
            elif same:
                reason = f"备注为「{label}」的版本生成失败，不能设为当前版本"
            else:
                reason = f"该样例没有备注为「{label}」的版本"
        else:
            same = [v for v in versions if (_cfg_model(v) or "") == model]
            hit = next((v for v in same if v.status == "succeeded"), None)
            if hit is not None:
                reason = None
            elif same:
                reason = f"用「{model}」生成的版本失败了，不能设为当前版本"
            else:
                reason = f"该样例没有用「{model}」生成的版本"
        if hit is None:
            items.append(BatchResolveItemOut(
                case_ref=ref, matched=False, reason=reason,
            ))
            continue
        items.append(BatchResolveItemOut(
            case_ref=ref,
            matched=True,
            already_current=(current_by_ref.get(ref) == hit.id),
            version_id=str(hit.id),
            version_number=int(hit.version_number),
            version_label=hit.version_label,
            model=_cfg_model(hit),
            status=hit.status,
        ))

    labels, numbers, models = _batch_options(grouped)
    matched = [i for i in items if i.matched]
    unchanged = [i for i in matched if i.already_current]
    return BatchResolveOut(
        total=len(items),
        matched_count=len(matched),
        changed_count=len(matched) - len(unchanged),
        unchanged_count=len(unchanged),
        missing_count=len(items) - len(matched),
        items=items,
        label_options=labels,
        version_number_options=numbers,
        model_options=models,
    )


# ───────────────────────────────────────────────────────────────────────────
# 批量切换当前版本
# ───────────────────────────────────────────────────────────────────────────


@router.post("/batch-resolve", response_model=BatchResolveOut)
async def batch_resolve_versions(req: BatchSetCurrentRequest):
    """干跑：这批样例按这个标识各自会切到哪个版本，谁切不了、为什么。

    前端弹窗先调它出预览（顺带拿到可选的版本备注 / 版本号下拉），用户确认后再
    调 /batch-set-current。两次走同一套解析逻辑，不会预览一套、执行另一套。
    """
    dataset_type = _check_dataset_type(req.dataset_type)
    async with async_session_factory() as session:
        repo = Repository(session)
        return await _resolve_batch_versions(
            repo,
            dataset_type=dataset_type,
            case_refs=req.case_refs,
            selector=req.selector,
        )


@router.post("/batch-set-current", response_model=BatchSetCurrentOut)
async def batch_set_current_versions(req: BatchSetCurrentRequest):
    """按批量标识把每个样例的当前版本指针挪到各自解析出的那条版本。

    部分成功即提交：解析不到版本的样例原样不动（missing），本来就是当前版本的
    跳过（unchanged），只对真正要变的写。个别样例写失败计入 failed 并保留原因，
    不因此整批回滚 —— 批量切换不是原子语义，让能切的先切完更符合使用意图。
    """
    dataset_type = _check_dataset_type(req.dataset_type)
    async with async_session_factory() as session:
        repo = Repository(session)
        preview = await _resolve_batch_versions(
            repo,
            dataset_type=dataset_type,
            case_refs=req.case_refs,
            selector=req.selector,
        )
        changed = 0
        failed = 0
        out_items: list[BatchResolveItemOut] = []
        for item in preview.items:
            if not item.matched or item.already_current:
                out_items.append(item)
                continue
            state = await repo.set_current_agent_reply_version(
                dataset_type=dataset_type,
                case_ref=item.case_ref,
                version_id=uuid.UUID(str(item.version_id)),
            )
            if state is None:
                failed += 1
                out_items.append(item.model_copy(update={
                    "matched": False,
                    "reason": "版本与样例不匹配，未切换",
                }))
                continue
            changed += 1
            out_items.append(item)
        await session.commit()
        return BatchSetCurrentOut(
            total=preview.total,
            changed_count=changed,
            unchanged_count=preview.unchanged_count,
            missing_count=preview.missing_count,
            failed_count=failed,
            items=out_items,
        )


# ───────────────────────────────────────────────────────────────────────────
# 生成任务
# ───────────────────────────────────────────────────────────────────────────


@router.post("/generate")
async def generate_replies(
    req: GenerateRepliesRequest,
    user: UserRow | None = Depends(require_internal()),
):
    """发起一次批量生成。服务端异步跑，立刻返回 job_id。

    同一样例 + 同一 agent 配置已有在途任务时返回 409 并列出冲突样例（前端弹窗
    阻止）；不同配置可并行，故去重键含配置指纹。
    """
    dataset_type = _check_dataset_type(req.dataset_type)
    agent_cfg = req.agent.model_dump()
    fingerprint = reply_generator.config_fingerprint(agent_cfg)
    version_label = _default_version_label(req.version_label, agent_cfg)
    project_id: uuid.UUID | None = None
    if req.project_id:
        try:
            project_id = uuid.UUID(req.project_id)
        except (TypeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=f"非法 project_id：{e}") from e

    async with async_session_factory() as session:
        repo = Repository(session)
        cases = await _resolve_cases(
            dataset_type=dataset_type,
            case_ids=req.case_ids,
            dataset_name=req.dataset_name,
            session=session,
        )
        refs = [str(c["id"]) for c in cases]
        inflight = await repo.find_inflight_job_items(
            dataset_type=dataset_type,
            case_refs=refs,
            config_fingerprint=fingerprint,
        )
        if inflight:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "选中样例中有正在生成的任务（相同 Agent 配置），"
                               "请等待完成或改用其他配置",
                    "case_refs": sorted({i.case_ref for i in inflight}),
                    "job_ids": sorted({str(i.job_id) for i in inflight}),
                },
            )

    ctx = get_tenant_context()
    job_id = await reply_generator.start_generation_job(
        dataset_type=dataset_type,
        cases=cases,
        agent_cfg=agent_cfg,
        dataset_name=req.dataset_name,
        project_id=project_id,
        version_label=version_label,
        concurrency=req.concurrency,
        created_by=(user.id if user is not None else None),
        tenant_ctx=ctx,
    )
    return {"job_id": job_id, "status": "running", "case_count": len(cases)}


@router.get("/jobs", response_model=list[JobOut])
async def list_jobs(
    dataset_type: str | None = Query(default=None),
    dataset_name: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    active_only: bool = Query(default=False),
    limit: int = Query(default=10, ge=1, le=50),
):
    pid: uuid.UUID | None = None
    if project_id:
        try:
            pid = uuid.UUID(project_id)
        except (TypeError, ValueError):
            pid = None
    async with async_session_factory() as session:
        repo = Repository(session)
        jobs = await repo.list_agent_reply_jobs(
            dataset_type=dataset_type,
            dataset_name=dataset_name,
            project_id=pid,
            active_only=active_only,
            limit=limit,
        )
        out: list[JobOut] = []
        uids = {j.created_by for j in jobs if j.created_by}
        names = await _load_user_names(repo, uids)
        for j in jobs:
            items = await repo.list_agent_reply_job_items(j.id)
            out.append(_job_out(j, items, names))
        return out


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: str):
    try:
        jid = uuid.UUID(job_id)
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"非法 job_id：{e}") from e
    async with async_session_factory() as session:
        repo = Repository(session)
        job = await repo.get_agent_reply_job(jid)
        if job is None:
            raise HTTPException(status_code=404, detail="生成任务不存在")
        items = await repo.list_agent_reply_job_items(jid)
        names = await _load_user_names(
            repo, {job.created_by} if job.created_by else set()
        )
        return _job_out(job, items, names)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    """取消剩余样例。已在跑的那条跑完为止；成功项不回滚。"""
    try:
        jid = uuid.UUID(job_id)
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"非法 job_id：{e}") from e
    async with async_session_factory() as session:
        repo = Repository(session)
        job = await repo.get_agent_reply_job(jid)
        if job is None:
            raise HTTPException(status_code=404, detail="生成任务不存在")
        if job.status != "running":
            return {"job_id": job_id, "status": job.status, "cancelled": False}
        job.cancel_requested = True
        await session.commit()
    ok = reply_generator.request_cancel(job_id)
    return {"job_id": job_id, "status": "cancelling", "cancelled": ok}


@router.post("/jobs/{job_id}/retry-failed")
async def retry_failed(
    job_id: str,
    user: UserRow | None = Depends(require_internal()),
):
    """重试该任务里所有 failed / cancelled 的样例，开一个新任务。"""
    try:
        jid = uuid.UUID(job_id)
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"非法 job_id：{e}") from e
    async with async_session_factory() as session:
        repo = Repository(session)
        job = await repo.get_agent_reply_job(jid)
        if job is None:
            raise HTTPException(status_code=404, detail="生成任务不存在")
        items = await repo.list_agent_reply_job_items(jid)
        refs = [i.case_ref for i in items if i.status in ("failed", "cancelled")]
        if not refs:
            raise HTTPException(status_code=400, detail="该任务没有失败或已取消的样例")
        # agent_config 快照里的 api_key 已脱敏，重试沿用脱敏后的配置只能在
        # 无凭证的本地/内网 agent 上成立。需要凭证时前端应重新提交生成表单。
        agent_cfg = dict(job.agent_config or {})
        cases = await _resolve_cases(
            dataset_type=job.dataset_type,
            case_ids=refs,
            dataset_name=job.dataset_name,
            session=session,
        )
        version_label = _default_version_label(job.version_label, agent_cfg)
        dataset_type = job.dataset_type
        dataset_name = job.dataset_name
        project_id = job.project_id

    ctx = get_tenant_context()
    new_job_id = await reply_generator.start_generation_job(
        dataset_type=dataset_type,
        cases=cases,
        agent_cfg=agent_cfg,
        dataset_name=dataset_name,
        project_id=project_id,
        version_label=version_label,
        created_by=(user.id if user is not None else None),
        tenant_ctx=ctx,
    )
    return {"job_id": new_job_id, "status": "running", "case_count": len(cases)}


@router.post("/retry-case")
async def retry_single_case(
    req: GenerateRepliesRequest,
    user: UserRow | None = Depends(require_internal()),
):
    """单条重试 = 用同一表单只提交一个样例，语义与 /generate 完全一致。

    单独开端点是为了让前端「重试这一条」按钮的意图在 API 层可读，避免把
    「批量生成」和「单条重试」混在同一路径上难以排查。
    """
    if len(req.case_ids) != 1:
        raise HTTPException(status_code=400, detail="单条重试只接受一个样例 id")
    return await generate_replies(req, user)


# ───────────────────────────────────────────────────────────────────────────
# 版本管理
# ───────────────────────────────────────────────────────────────────────────


@router.get("/states", response_model=list[CaseReplyStateOut])
async def list_case_states(
    dataset_type: str = Query(...),
    case_refs: str = Query(..., description="逗号分隔的样例 id 列表"),
):
    """批量查一批样例的回复状态（列表页给每行打「已生成 / N 个版本」标记）。"""
    _check_dataset_type(dataset_type)
    refs = [x for x in (case_refs or "").split(",") if x]
    if not refs:
        return []
    async with async_session_factory() as session:
        repo = Repository(session)
        states = await repo.list_agent_reply_case_states(dataset_type, refs)
        counts = await repo.count_agent_reply_versions_by_case(dataset_type, refs)
        by_ref = {s.case_ref: s for s in states}
        current_ids = [
            s.current_version_id for s in states if s.current_version_id is not None
        ]
        current_map: dict[str, Any] = {}
        for vid in current_ids:
            row = await repo.get_agent_reply_version(vid)
            if row is not None:
                current_map[str(vid)] = row
        out: list[CaseReplyStateOut] = []
        for ref in refs:
            st = by_ref.get(ref)
            cur = (
                current_map.get(str(st.current_version_id))
                if st is not None and st.current_version_id is not None
                else None
            )
            out.append(CaseReplyStateOut(
                case_ref=ref,
                has_reply=cur is not None,
                current_version_id=str(cur.id) if cur is not None else None,
                current_version_number=cur.version_number if cur is not None else None,
                current_version_label=cur.version_label if cur is not None else None,
                current_model=_cfg_model(cur) if cur is not None else None,
                version_count=int(counts.get(ref, 0)),
            ))
        return out


@router.get("/versions", response_model=list[ReplyVersionOut])
async def list_versions(
    dataset_type: str = Query(...),
    case_ref: str = Query(...),
):
    """某样例的全部版本，最新在前。带 is_current 与被评估引用计数。"""
    _check_dataset_type(dataset_type)
    async with async_session_factory() as session:
        repo = Repository(session)
        rows = await repo.list_agent_reply_versions(dataset_type, case_ref)
        state = await repo.get_agent_reply_case_state(dataset_type, case_ref)
        current_id = state.current_version_id if state is not None else None
        names = await _load_user_names(
            repo, {r.created_by for r in rows if r.created_by}
        )
        out: list[ReplyVersionOut] = []
        for r in rows:
            used = await repo.count_results_using_reply_version(r.id)
            out.append(_version_out(
                r,
                is_current=(current_id is not None and r.id == current_id),
                used_by_results=used,
                user_names=names,
            ))
        return out


@router.patch("/versions/{version_id}", response_model=ReplyVersionOut)
async def update_version(version_id: str, req: UpdateVersionRequest):
    """编辑版本内容 / 版本号。编辑后打 edited 标记，保留原始 raw_trace。"""
    try:
        vid = uuid.UUID(version_id)
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"非法 version_id：{e}") from e
    fields: dict[str, Any] = {}
    if req.content is not None:
        fields["content"] = req.content
        fields["edited"] = True
    if req.version_label is not None:
        fields["version_label"] = req.version_label
    if req.turns is not None:
        fields["turns"] = req.turns
        fields["edited"] = True
    if not fields:
        raise HTTPException(status_code=400, detail="没有要更新的字段")
    async with async_session_factory() as session:
        repo = Repository(session)
        row = await repo.update_agent_reply_version(vid, **fields)
        if row is None:
            raise HTTPException(status_code=404, detail="回复版本不存在")
        state = await repo.get_agent_reply_case_state(row.dataset_type, row.case_ref)
        used = await repo.count_results_using_reply_version(row.id)
        names = await _load_user_names(
            repo, {row.created_by} if row.created_by else set()
        )
        await session.commit()
        return _version_out(
            row,
            is_current=(
                state is not None and state.current_version_id == row.id
            ),
            used_by_results=used,
            user_names=names,
        )


@router.post("/versions/{version_id}/set-current", response_model=ReplyVersionOut)
async def set_current_version(version_id: str):
    """把该版本设为当前版本（评估选「当前版本」时消费的就是它）。"""
    try:
        vid = uuid.UUID(version_id)
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"非法 version_id：{e}") from e
    async with async_session_factory() as session:
        repo = Repository(session)
        row = await repo.get_agent_reply_version(vid)
        if row is None:
            raise HTTPException(status_code=404, detail="回复版本不存在")
        if row.status != "succeeded":
            raise HTTPException(
                status_code=400, detail="生成失败的版本不能设为当前版本",
            )
        state = await repo.set_current_agent_reply_version(
            dataset_type=row.dataset_type,
            case_ref=row.case_ref,
            version_id=vid,
        )
        if state is None:
            raise HTTPException(status_code=400, detail="版本与样例不匹配")
        used = await repo.count_results_using_reply_version(row.id)
        names = await _load_user_names(
            repo, {row.created_by} if row.created_by else set()
        )
        await session.commit()
        return _version_out(
            row, is_current=True, used_by_results=used, user_names=names,
        )


@router.delete("/versions/{version_id}")
async def delete_version(version_id: str):
    """删除一个版本。

    保护规则：
    - 已被某次评估固定引用（test_results.reply_version_id）→ 409，不允许删，
      否则历史结果无法复现。
    - 删的是当前版本 → 指针改指剩余版本里最新的那条。
    - 删的是最后一个版本 → 连 case_state 行一起删掉（等价于「该样例无回复」）。
    """
    try:
        vid = uuid.UUID(version_id)
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"非法 version_id：{e}") from e
    async with async_session_factory() as session:
        repo = Repository(session)
        row = await repo.get_agent_reply_version(vid)
        if row is None:
            raise HTTPException(status_code=404, detail="回复版本不存在")
        used = await repo.count_results_using_reply_version(vid)
        if used > 0:
            raise HTTPException(
                status_code=409,
                detail=f"该版本已被 {used} 条评估结果引用，删除会导致历史不可复现",
            )
        dataset_type, case_ref = row.dataset_type, row.case_ref
        await repo.delete_agent_reply_version(vid)
        remaining = await repo.list_agent_reply_versions(dataset_type, case_ref)
        state = await repo.get_agent_reply_case_state(dataset_type, case_ref)
        await session.commit()
        return {
            "deleted": True,
            "remaining_count": len(remaining),
            "current_version_id": (
                str(state.current_version_id)
                if state is not None and state.current_version_id
                else None
            ),
        }
