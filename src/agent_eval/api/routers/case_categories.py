"""样例类别的批量修改 API（横跨三类数据集）。

三类数据集的「类别」落在三处完全不同的存储上：

  candidate     ``candidate_cases.category``     自由文本名，允许新名
  benchmark     ``benchmark_cases.category_id``  FK → ``categories``（按 project 划分）
  conversation  Langfuse dataset item 的 ``metadata.category``  受管名，无外键

所以批量改类别不是一条 UPDATE 能覆盖的事。这里用与 ``agent_replies`` 批量切版本
相同的「干跑预览 + 执行」成对端点：``/batch-resolve`` 先算出每条样例会从什么改成
什么、谁改不了以及为什么，用户确认后再调 ``/batch-set`` 落库。两端共用同一套解析
逻辑，不会预览一套、执行另一套。

部分成功即提交：改不了的样例（不存在 / 目标类别不适用）原样留下并带 reason，本来
就是目标类别的跳过，只对真正要变的写；个别写失败计入 failed 并保留原因，不因此回滚
整批 —— 批量改类别不是原子语义，让能改的先改完更符合使用意图。

权限：router 级 ``require_internal()``，与 benchmark / candidates / cases 同域。

样例引用统一是 ``(dataset_type, case_ref)``：
  candidate / benchmark → 本地表主键的字符串形式
  conversation          → Langfuse dataset item id
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from agent_eval.api.dependencies import get_manager
from agent_eval.auth.dependencies import require_internal
from agent_eval.data.dataset_manager import DatasetManager
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import (
    BenchmarkCaseRow,
    CandidateCaseRow,
    CategoryRow,
    ConversationCategoryRow,
)
from agent_eval.governance.helpers import log_audit
from agent_eval.models.test_case import TestCase

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/case-categories",
    tags=["case-categories"],
    dependencies=[Depends(require_internal())],
)

_DATASET_TYPES = ("candidate", "benchmark", "conversation")
# 分布里「没有类别」这一档的显示名。仅用于展示，不会被写进任何存储。
_UNCATEGORIZED = "(未分类)"
# candidate_cases.category 是 String(128)，超长直接 400 而不是让 DB 抛。
_MAX_CATEGORY_NAME_LEN = 128


# ───────────────────────────────────────────────────────────────────────────
# Schemas
# ───────────────────────────────────────────────────────────────────────────


class CategoryTarget(BaseModel):
    """批量要把类别改成什么。

    - ``mode='clear'``  清空（三类分别写 NULL / NULL / 抹掉 metadata.category）
    - ``mode='set'``    设成指定类别；取值按数据集类型分流：
        benchmark               用 ``category_id``（必须是样例所属 project 下已有的类别）
        candidate               用 ``category_name``（自由文本，允许全新的名字）
        conversation            用 ``category_name``（必须是该数据集已有的受管类别名）

    基准集与多轮对话集都不在这里隐式新建类别 —— 批量改类别只在既有类别之间搬动或
    清空，新建类别走各自页面的类别管理入口，避免手滑打错字凭空造出一个类别。
    """

    mode: str = "set"
    category_id: str | None = None
    category_name: str | None = None


class BatchCategoryRequest(BaseModel):
    dataset_type: str
    case_refs: list[str] = Field(min_length=1, max_length=2000)
    # 多轮对话集必填：受管类别校验和一次性全量读都要它。
    dataset_name: str | None = None
    target: CategoryTarget = CategoryTarget()


class BatchCategoryItemOut(BaseModel):
    """单条样例的解析结果。matched=False 时 reason 说明为什么改不了。"""

    case_ref: str
    matched: bool
    already_current: bool = False
    current_category: str | None = None
    target_category: str | None = None
    reason: str | None = None


class BatchOptionOut(BaseModel):
    """一个类别及其在本批勾选样例里的当前条数（前端下拉 / 分布直接用）。

    ``value_id`` 只有基准集有意义（提交时要回传 category_id）；candidate /
    conversation 按名字提交，为 None。
    """

    value: str
    case_count: int
    value_id: str | None = None


class BatchCategoryResolveOut(BaseModel):
    total: int
    matched_count: int
    changed_count: int
    unchanged_count: int
    missing_count: int
    items: list[BatchCategoryItemOut]
    # 本批样例当前的类别分布（含 _UNCATEGORIZED 一档），给用户「我选中的是些什么」。
    current_distribution: list[BatchOptionOut] = []
    # 可作为目标的类别全集，与后端校验口径一致，前端下拉直接用。
    category_options: list[BatchOptionOut] = []


class BatchCategorySetOut(BaseModel):
    total: int
    changed_count: int
    unchanged_count: int
    missing_count: int
    failed_count: int
    items: list[BatchCategoryItemOut]


# ───────────────────────────────────────────────────────────────────────────
# 解析
# ───────────────────────────────────────────────────────────────────────────


def _check_dataset_type(dataset_type: str) -> str:
    if dataset_type not in _DATASET_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"dataset_type 必须是 {' / '.join(_DATASET_TYPES)} 之一",
        )
    return dataset_type


def _normalize_target(dataset_type: str, target: CategoryTarget) -> tuple[str, str]:
    """校验并归一化目标，返回 ``(mode, raw_value)``。

    raw_value 是 mode='set' 时用户给的原始标识（benchmark 是 category_id，其余是
    类别名），mode='clear' 时为空串。校验不通过直接 400 —— 这类错是「请求本身不
    成立」，不该退化成每条样例各自 matched=False。
    """
    mode = (target.mode or "set").strip()
    if mode not in ("set", "clear"):
        raise HTTPException(status_code=400, detail="target.mode 必须是 set / clear 之一")
    if mode == "clear":
        return mode, ""
    if dataset_type == "benchmark":
        raw = (target.category_id or "").strip()
        if not raw:
            raise HTTPException(
                status_code=400, detail="基准集批量设置类别必须提供 category_id",
            )
    else:
        raw = (target.category_name or "").strip()
        if not raw:
            raise HTTPException(
                status_code=400, detail="批量设置类别必须提供 category_name",
            )
        if len(raw) > _MAX_CATEGORY_NAME_LEN:
            raise HTTPException(
                status_code=400,
                detail=f"类别名过长（最多 {_MAX_CATEGORY_NAME_LEN} 个字符）",
            )
    return mode, raw


def _distribution(items: list[BatchCategoryItemOut]) -> list[BatchOptionOut]:
    """本批样例的当前类别分布。只统计存在的样例（matched 的那些）。"""
    counter: dict[str, int] = {}
    for item in items:
        if not item.matched:
            continue
        key = item.current_category or _UNCATEGORIZED
        counter[key] = counter.get(key, 0) + 1
    return [
        BatchOptionOut(value=k, case_count=v)
        for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def _summarize(
    items: list[BatchCategoryItemOut],
    *,
    distribution: list[BatchOptionOut],
    options: list[BatchOptionOut],
) -> BatchCategoryResolveOut:
    matched = [i for i in items if i.matched]
    unchanged = [i for i in matched if i.already_current]
    return BatchCategoryResolveOut(
        total=len(items),
        matched_count=len(matched),
        changed_count=len(matched) - len(unchanged),
        unchanged_count=len(unchanged),
        missing_count=len(items) - len(matched),
        items=items,
        current_distribution=distribution,
        category_options=options,
    )


def _count_by_current(
    items: list[BatchCategoryItemOut], name: str | None,
) -> int:
    return sum(1 for i in items if i.matched and i.current_category == name)


async def _resolve_candidate(
    session,
    *,
    refs: list[str],
    mode: str,
    raw: str,
    dataset_name: str | None,
) -> tuple[BatchCategoryResolveOut, dict[str, Any]]:
    """备选集：自由文本类别名，目标名不必事先存在。"""
    target_name = None if mode == "clear" else raw
    by_ref: dict[str, CandidateCaseRow] = {}
    ids: list[uuid.UUID] = []
    bad_refs: set[str] = set()
    for ref in refs:
        try:
            ids.append(uuid.UUID(ref))
        except (TypeError, ValueError):
            bad_refs.add(ref)
    if ids:
        result = await session.execute(
            select(CandidateCaseRow).where(CandidateCaseRow.id.in_(ids))
        )
        by_ref = {str(r.id): r for r in result.scalars().all()}

    items: list[BatchCategoryItemOut] = []
    for ref in refs:
        if ref in bad_refs:
            items.append(BatchCategoryItemOut(
                case_ref=ref, matched=False, reason="非法样例 ID",
            ))
            continue
        row = by_ref.get(ref)
        if row is None:
            items.append(BatchCategoryItemOut(
                case_ref=ref, matched=False, reason="样例不存在或无权访问",
            ))
            continue
        current = row.category or None
        items.append(BatchCategoryItemOut(
            case_ref=ref,
            matched=True,
            already_current=(current == target_name),
            current_category=current,
            target_category=target_name,
        ))

    # 可选类别：该数据集下已出现过的类别名（与 /api/candidates/categories 同口径）。
    stmt = select(CandidateCaseRow.category).where(
        CandidateCaseRow.category.isnot(None)
    )
    if dataset_name:
        stmt = stmt.where(CandidateCaseRow.dataset_name == dataset_name)
    existing = await session.execute(stmt.distinct().order_by(CandidateCaseRow.category))
    options = [
        BatchOptionOut(value=name, case_count=_count_by_current(items, name))
        for name in existing.scalars().all()
        if name and name.strip()
    ]
    out = _summarize(items, distribution=_distribution(items), options=options)
    return out, {"rows": by_ref, "target_name": target_name}


async def _resolve_benchmark(
    session,
    *,
    refs: list[str],
    mode: str,
    raw: str,
) -> tuple[BatchCategoryResolveOut, dict[str, Any]]:
    """基准集：category_id 外键，且类别按 project 划分，需逐条校验归属。"""
    target_row: CategoryRow | None = None
    if mode == "set":
        try:
            target_uuid = uuid.UUID(raw)
        except (TypeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=f"非法 category_id：{e}") from e
        found = await session.execute(
            select(CategoryRow).where(CategoryRow.id == target_uuid)
        )
        target_row = found.scalar_one_or_none()
        if target_row is None:
            raise HTTPException(status_code=404, detail="目标类别不存在")
    target_name = target_row.name if target_row is not None else None

    by_ref: dict[str, BenchmarkCaseRow] = {}
    ids: list[uuid.UUID] = []
    bad_refs: set[str] = set()
    for ref in refs:
        try:
            ids.append(uuid.UUID(ref))
        except (TypeError, ValueError):
            bad_refs.add(ref)
    if ids:
        result = await session.execute(
            select(BenchmarkCaseRow).where(BenchmarkCaseRow.id.in_(ids))
        )
        by_ref = {str(r.id): r for r in result.scalars().all()}

    # 当前类别名要额外查一次 categories（样例上只有 id）。
    cat_ids = {r.category_id for r in by_ref.values() if r.category_id}
    name_by_id: dict[uuid.UUID, str] = {}
    project_ids = {r.project_id for r in by_ref.values() if r.project_id}
    if cat_ids:
        cats = await session.execute(
            select(CategoryRow).where(CategoryRow.id.in_(cat_ids))
        )
        name_by_id = {c.id: c.name for c in cats.scalars().all()}

    items: list[BatchCategoryItemOut] = []
    for ref in refs:
        if ref in bad_refs:
            items.append(BatchCategoryItemOut(
                case_ref=ref, matched=False, reason="非法样例 ID",
            ))
            continue
        row = by_ref.get(ref)
        if row is None:
            items.append(BatchCategoryItemOut(
                case_ref=ref, matched=False, reason="样例不存在或无权访问",
            ))
            continue
        current = name_by_id.get(row.category_id) if row.category_id else None
        if target_row is not None and target_row.project_id != row.project_id:
            # 类别是 project 内唯一的，跨 project 挪过去会挂上别人的类别。
            items.append(BatchCategoryItemOut(
                case_ref=ref,
                matched=False,
                current_category=current,
                reason=f"类别「{target_row.name}」不属于该样例所在项目",
            ))
            continue
        items.append(BatchCategoryItemOut(
            case_ref=ref,
            matched=True,
            already_current=(
                (row.category_id or None)
                == (target_row.id if target_row is not None else None)
            ),
            current_category=current,
            target_category=target_name,
        ))

    # 可选类别：这批样例所在 project 下的全部类别（附 category_id 供前端回传）。
    options: list[BatchOptionOut] = []
    if project_ids:
        cats = await session.execute(
            select(CategoryRow)
            .where(CategoryRow.project_id.in_(project_ids))
            .order_by(CategoryRow.name)
        )
        options = [
            BatchOptionOut(
                value=c.name,
                case_count=_count_by_current(items, c.name),
                value_id=str(c.id),
            )
            for c in cats.scalars().all()
        ]
    out = _summarize(items, distribution=_distribution(items), options=options)
    return out, {
        "rows": by_ref,
        "target_id": target_row.id if target_row is not None else None,
        "target_name": target_name,
    }


async def _resolve_conversation(
    session,
    mgr: DatasetManager,
    *,
    refs: list[str],
    mode: str,
    raw: str,
    dataset_name: str | None,
) -> tuple[BatchCategoryResolveOut, dict[str, Any]]:
    """多轮对话集：类别在 Langfuse metadata 里，受管名单存在本地表。

    没有外键也没有批量写接口，所以只能逐条 update_case（与类别重命名时的同步
    做法一致）。读这边一次性 load_cases 拿全量快照，避免 N 次 get_case。
    """
    if not dataset_name:
        raise HTTPException(
            status_code=400, detail="多轮对话集批量改类别必须提供 dataset_name",
        )

    managed = await session.execute(
        select(ConversationCategoryRow)
        .where(ConversationCategoryRow.dataset_name == dataset_name)
        .order_by(ConversationCategoryRow.name)
    )
    managed_names = [c.name for c in managed.scalars().all()]
    target_name = None
    if mode == "set":
        if raw not in managed_names:
            raise HTTPException(
                status_code=404,
                detail=f"数据集「{dataset_name}」下没有类别「{raw}」，请先在类别管理里创建",
            )
        target_name = raw

    try:
        cases = await mgr.load_cases(dataset_name)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"读取样例失败：{e}") from e
    by_ref: dict[str, TestCase] = {str(c.id): c for c in cases}

    items: list[BatchCategoryItemOut] = []
    for ref in refs:
        case = by_ref.get(ref)
        if case is None:
            items.append(BatchCategoryItemOut(
                case_ref=ref, matched=False, reason="样例不在该数据集中",
            ))
            continue
        current = case.category or None
        items.append(BatchCategoryItemOut(
            case_ref=ref,
            matched=True,
            already_current=(current == target_name),
            current_category=current,
            target_category=target_name,
        ))

    options = [
        BatchOptionOut(value=name, case_count=_count_by_current(items, name))
        for name in managed_names
    ]
    out = _summarize(items, distribution=_distribution(items), options=options)
    return out, {"cases": by_ref, "target_name": target_name}


async def _resolve(
    session,
    mgr: DatasetManager,
    *,
    dataset_type: str,
    case_refs: list[str],
    dataset_name: str | None,
    target: CategoryTarget,
) -> tuple[BatchCategoryResolveOut, dict[str, Any]]:
    """把一次批量意图解析成逐条「从什么改成什么」。预览与执行共用。"""
    mode, raw = _normalize_target(dataset_type, target)
    refs = list(dict.fromkeys(case_refs))
    if dataset_type == "candidate":
        return await _resolve_candidate(
            session, refs=refs, mode=mode, raw=raw, dataset_name=dataset_name,
        )
    if dataset_type == "benchmark":
        return await _resolve_benchmark(session, refs=refs, mode=mode, raw=raw)
    return await _resolve_conversation(
        session, mgr, refs=refs, mode=mode, raw=raw, dataset_name=dataset_name,
    )


# ───────────────────────────────────────────────────────────────────────────
# 端点
# ───────────────────────────────────────────────────────────────────────────


@router.post("/batch-resolve", response_model=BatchCategoryResolveOut)
async def batch_resolve_categories(
    req: BatchCategoryRequest,
    mgr: DatasetManager = Depends(get_manager),
):
    """干跑：这批样例会从什么类别改成什么，谁改不了、为什么。

    前端弹窗先调它出预览（顺带拿到可选类别下拉与本批当前分布），用户确认后再调
    /batch-set。两次走同一套解析逻辑，不会预览一套、执行另一套。
    """
    dataset_type = _check_dataset_type(req.dataset_type)
    async with async_session_factory() as session:
        out, _ = await _resolve(
            session,
            mgr,
            dataset_type=dataset_type,
            case_refs=req.case_refs,
            dataset_name=req.dataset_name,
            target=req.target,
        )
        return out


@router.post("/batch-set", response_model=BatchCategorySetOut)
async def batch_set_categories(
    req: BatchCategoryRequest,
    mgr: DatasetManager = Depends(get_manager),
):
    """把这批样例的类别批量设成目标类别（或清空）。

    部分成功即提交：改不了的样例原样不动（missing），本来就是目标类别的跳过
    （unchanged），只对真正要变的写。个别样例写失败计入 failed 并保留原因，不因此
    回滚整批 —— 多轮对话集是逐条打 Langfuse，本来就没有整批原子性可言，基准集与
    备选集也统一按同一语义，避免同一个按钮在三类数据集上行为不一致。
    """
    dataset_type = _check_dataset_type(req.dataset_type)
    async with async_session_factory() as session:
        preview, ctx = await _resolve(
            session,
            mgr,
            dataset_type=dataset_type,
            case_refs=req.case_refs,
            dataset_name=req.dataset_name,
            target=req.target,
        )
        todo = [i for i in preview.items if i.matched and not i.already_current]
        changed = 0
        failed = 0
        failed_reason: dict[str, str] = {}
        now = datetime.now(timezone.utc)

        if dataset_type == "conversation":
            cases: dict[str, TestCase] = ctx["cases"]
            for item in todo:
                case = cases[item.case_ref]
                case.category = ctx["target_name"]
                try:
                    await mgr.update_case(item.case_ref, case)
                    changed += 1
                except Exception as e:
                    # 逐条容错：一条打不进去不该让其余样例白跑一遍。
                    logger.warning(
                        "批量改类别失败 item=%s: %s", item.case_ref, e,
                    )
                    failed += 1
                    failed_reason[item.case_ref] = f"写入失败：{e}"
        else:
            rows = ctx["rows"]
            for item in todo:
                row = rows[item.case_ref]
                if dataset_type == "benchmark":
                    row.category_id = ctx["target_id"]
                else:
                    row.category = ctx["target_name"]
                row.updated_at = now
                changed += 1
            await session.commit()

        out_items = [
            i.model_copy(update={
                "matched": False,
                "already_current": False,
                "reason": failed_reason[i.case_ref],
            })
            if i.case_ref in failed_reason else i
            for i in preview.items
        ]

    await log_audit(
        "case_category",
        req.dataset_name or dataset_type,
        "batch_set",
        details={
            "dataset_type": dataset_type,
            "mode": (req.target.mode or "set"),
            "target": ctx.get("target_name"),
            "total": preview.total,
            "changed": changed,
            "failed": failed,
        },
    )
    return BatchCategorySetOut(
        total=preview.total,
        changed_count=changed,
        unchanged_count=preview.unchanged_count,
        missing_count=preview.missing_count,
        failed_count=failed,
        items=out_items,
    )
