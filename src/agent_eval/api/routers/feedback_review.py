"""内部反馈展示（feedback-api 摊）。

外部客户在 portal 对样例的手动打分 + 意见，回流到内部入口的这个模块给
**内部角色（admin + 内部普通 user）** 查看。

跨租户可见的实现 —— 关键点：反馈数据落在各**外部客户租户**里，不在内部租户。
内部 admin 登录态是 superadmin，db.py 的读监听器对 superadmin **旁路过滤**，
天然能跨租户看到全部。但内部普通 user **不是** superadmin，get_current_user
会把租户上下文设为 ``(INTERNAL, superadmin=False)``，读监听器会注入
``tenant_id == INTERNAL`` 把反馈查询过滤成空。

因此本 router 用 ``_internal_crosstenant`` 依赖：先经 ``require_internal()``
校验为内部角色（external_customer 被 403 挡掉），再把租户上下文**覆盖为
superadmin 旁路**，使内部普通 user 也能跨租户读反馈 —— 与 admin 行为一致。
所以下方各端点直连 ``async_session_factory()`` 查 TenantMixin 表
（batches/samples/feedbacks）即可跨租户，无需手写 ``.where(tenant_id==...)``。

可选 ``tenant_id`` 过滤是「想只看某租户」时由调用方显式追加的 where —— 因为
旁路后默认不被过滤，不显式加就是全租户。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select

from agent_eval.auth.dependencies import require_internal
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import (
    PortalSampleBatchRow,
    PortalSampleRow,
    SampleFeedbackRow,
    TenantRow,
    UserRow,
)
from agent_eval.db_models.tenant_context import (
    INTERNAL_TENANT_ID,
    TenantContext,
    reset_tenant_context,
    set_tenant_context,
)


async def _internal_crosstenant(
    user: UserRow | None = Depends(require_internal()),
) -> AsyncIterator[UserRow | None]:
    """内部角色（admin|user）放行 + 跨租户旁路。

    ``require_internal()`` 先校验角色：external_customer 拿 403，到不了这里；
    auth 关闭（dev 模式）时 user 为 None（已是无上下文旁路），也无妨。

    随后把租户上下文覆盖为 ``superadmin=True``：内部 admin 本就旁路、内部普通
    user 借此也能跨租户读反馈。请求结束后 reset 回 get_current_user 设的原
    上下文（再由其 reset 回 None），ContextVar token 链保证不跨请求泄漏。
    """
    token = set_tenant_context(TenantContext(INTERNAL_TENANT_ID, superadmin=True))
    try:
        yield user
    finally:
        reset_tenant_context(token)


router = APIRouter(
    prefix="/api/feedback",
    tags=["feedback-review"],
    dependencies=[Depends(_internal_crosstenant)],
)


def _round(value: float | None, ndigits: int = 2) -> float | None:
    """func.avg 在 asyncpg 下返回 Decimal；统一转 float 再四舍五入。None 透传。"""
    if value is None:
        return None
    return round(float(value), ndigits)


def _coverage(rated: int, total: int) -> float:
    """已评样例占比；total=0 时返回 0 避免除零。"""
    if total <= 0:
        return 0.0
    return round(rated / total, 4)


# --------------------------------------------------------------------------- #
# 响应模型
# --------------------------------------------------------------------------- #
class FeedbackBatchSummary(BaseModel):
    """一个批次的反馈聚合。"""

    batch_id: uuid.UUID
    name: str
    tenant_id: uuid.UUID
    tenant_name: str | None = None
    status: str
    created_at: str
    sample_count: int  # 批次内样例总数
    rated_sample_count: int  # 已被至少一人评过的样例数
    feedback_count: int  # 反馈条目总数（一样例可被多人评）
    avg_overall: float | None  # 平均总体分（1-5）
    coverage: float  # rated_sample_count / sample_count


class FeedbackBatchListResponse(BaseModel):
    tenant_id: uuid.UUID | None = None  # 回显请求的过滤条件
    returned: int
    batches: list[FeedbackBatchSummary]


class FeedbackSampleRowItem(BaseModel):
    """批次下钻样例列表的轻量行（含该样例反馈聚合）。"""

    id: uuid.UUID
    row_index: int
    question: str
    answer: str | None = None
    feedback_count: int
    avg_overall: float | None = None


class FeedbackBatchSamplesResponse(BaseModel):
    """某批次下的分页样例（含每条样例的反馈聚合）。"""

    batch_id: uuid.UUID
    batch_name: str | None = None
    tenant_id: uuid.UUID
    tenant_name: str | None = None
    total: int
    page: int
    page_size: int
    samples: list[FeedbackSampleRowItem]


class SampleFeedbackDetail(BaseModel):
    """单条客户反馈明细。"""

    id: uuid.UUID
    rated_by: uuid.UUID | None = None
    rated_by_username: str | None = None
    overall: int | None = None
    scores: dict
    comment: str | None = None
    expected_answer: str | None = None  # 评审人补写的期望答案（GroundTruth）
    created_at: str
    updated_at: str


class SampleFeedbackResponse(BaseModel):
    """单样例 + 其全部反馈。"""

    sample_id: uuid.UUID
    batch_id: uuid.UUID
    batch_name: str | None = None
    tenant_id: uuid.UUID
    tenant_name: str | None = None
    row_index: int
    question: str
    answer: str | None = None
    extra: dict
    feedback_count: int
    avg_overall: float | None = None
    feedbacks: list[SampleFeedbackDetail]


class TenantFeedbackStat(BaseModel):
    """按租户聚合的反馈概览。"""

    tenant_id: uuid.UUID
    tenant_name: str | None = None
    batch_count: int
    sample_count: int
    rated_sample_count: int
    feedback_count: int
    avg_overall: float | None = None
    coverage: float


class FeedbackStatsResponse(BaseModel):
    # 全局汇总
    total_tenants: int
    total_batches: int
    total_samples: int
    total_rated_samples: int
    total_feedbacks: int
    avg_overall: float | None = None
    coverage: float
    # 按租户拆分
    by_tenant: list[TenantFeedbackStat]


# --------------------------------------------------------------------------- #
# 端点
# --------------------------------------------------------------------------- #
@router.get("/batches", response_model=FeedbackBatchListResponse)
async def list_feedback_batches(
    tenant_id: uuid.UUID | None = Query(
        None, description="只看某租户；不传则跨租户列出全部有反馈的批次"
    ),
) -> FeedbackBatchListResponse:
    """跨租户列出**有反馈的**批次，附样例数 / 已评数 / 平均总体分聚合。

    只返回至少存在一条反馈的批次（靠 inner join 反馈聚合子查询实现）。
    """
    # 每批次的样例总数
    sample_count_sq = (
        select(
            PortalSampleRow.batch_id.label("batch_id"),
            func.count(PortalSampleRow.id).label("sample_count"),
        )
        .group_by(PortalSampleRow.batch_id)
        .subquery()
    )
    # 每批次的反馈聚合（经 sample 关联到 batch）
    fb_sq = (
        select(
            PortalSampleRow.batch_id.label("batch_id"),
            func.count(func.distinct(SampleFeedbackRow.sample_id)).label("rated_count"),
            func.count(SampleFeedbackRow.id).label("feedback_count"),
            func.avg(SampleFeedbackRow.overall).label("avg_overall"),
        )
        .join(SampleFeedbackRow, SampleFeedbackRow.sample_id == PortalSampleRow.id)
        .group_by(PortalSampleRow.batch_id)
        .subquery()
    )

    stmt = (
        select(
            PortalSampleBatchRow,
            TenantRow.name.label("tenant_name"),
            sample_count_sq.c.sample_count,
            fb_sq.c.rated_count,
            fb_sq.c.feedback_count,
            fb_sq.c.avg_overall,
        )
        # inner join：只保留有反馈的批次
        .join(fb_sq, fb_sq.c.batch_id == PortalSampleBatchRow.id)
        .outerjoin(sample_count_sq, sample_count_sq.c.batch_id == PortalSampleBatchRow.id)
        .outerjoin(TenantRow, TenantRow.id == PortalSampleBatchRow.tenant_id)
        .order_by(PortalSampleBatchRow.created_at.desc())
    )
    if tenant_id is not None:
        stmt = stmt.where(PortalSampleBatchRow.tenant_id == tenant_id)

    async with async_session_factory() as session:
        rows = (await session.execute(stmt)).all()

    batches: list[FeedbackBatchSummary] = []
    for batch, tenant_name, sample_count, rated_count, feedback_count, avg_overall in rows:
        total = int(sample_count or 0)
        rated = int(rated_count or 0)
        batches.append(
            FeedbackBatchSummary(
                batch_id=batch.id,
                name=batch.name,
                tenant_id=batch.tenant_id,
                tenant_name=tenant_name,
                status=batch.status,
                created_at=batch.created_at.isoformat(),
                sample_count=total,
                rated_sample_count=rated,
                feedback_count=int(feedback_count or 0),
                avg_overall=_round(avg_overall),
                coverage=_coverage(rated, total),
            )
        )

    return FeedbackBatchListResponse(
        tenant_id=tenant_id,
        returned=len(batches),
        batches=batches,
    )


@router.get(
    "/batches/{batch_id}/samples", response_model=FeedbackBatchSamplesResponse
)
async def list_batch_samples(
    batch_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
) -> FeedbackBatchSamplesResponse:
    """某批次下的分页样例，每条附其反馈数与平均总体分。

    从批次下钻到样例的中间层：批次列表 → 本端点 → 单样例反馈明细。
    """
    async with async_session_factory() as session:
        # 批次本体 + 租户名（superadmin 不被过滤，跨租户可读）
        batch_stmt = (
            select(
                PortalSampleBatchRow.name.label("batch_name"),
                PortalSampleBatchRow.tenant_id.label("tenant_id"),
                TenantRow.name.label("tenant_name"),
            )
            .outerjoin(TenantRow, TenantRow.id == PortalSampleBatchRow.tenant_id)
            .where(PortalSampleBatchRow.id == batch_id)
        )
        batch_row = (await session.execute(batch_stmt)).first()
        if batch_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found"
            )
        batch_name, tenant_id, tenant_name = batch_row

        total = (
            await session.execute(
                select(func.count(PortalSampleRow.id)).where(
                    PortalSampleRow.batch_id == batch_id
                )
            )
        ).scalar_one()

        # 每样例的反馈聚合
        fb_sq = (
            select(
                SampleFeedbackRow.sample_id.label("sample_id"),
                func.count(SampleFeedbackRow.id).label("feedback_count"),
                func.avg(SampleFeedbackRow.overall).label("avg_overall"),
            )
            .group_by(SampleFeedbackRow.sample_id)
            .subquery()
        )
        stmt = (
            select(
                PortalSampleRow,
                fb_sq.c.feedback_count,
                fb_sq.c.avg_overall,
            )
            .outerjoin(fb_sq, fb_sq.c.sample_id == PortalSampleRow.id)
            .where(PortalSampleRow.batch_id == batch_id)
            .order_by(PortalSampleRow.row_index.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = (await session.execute(stmt)).all()

    samples = [
        FeedbackSampleRowItem(
            id=sample.id,
            row_index=sample.row_index,
            question=sample.question,
            answer=sample.answer,
            feedback_count=int(feedback_count or 0),
            avg_overall=_round(avg_overall),
        )
        for sample, feedback_count, avg_overall in rows
    ]

    return FeedbackBatchSamplesResponse(
        batch_id=batch_id,
        batch_name=batch_name,
        tenant_id=tenant_id,
        tenant_name=tenant_name,
        total=int(total or 0),
        page=page,
        page_size=page_size,
        samples=samples,
    )


@router.get("/samples/{sample_id}", response_model=SampleFeedbackResponse)
async def get_sample_feedback(sample_id: uuid.UUID) -> SampleFeedbackResponse:
    """单个样例 + 其所有客户反馈明细（含评分人用户名）。"""
    async with async_session_factory() as session:
        # 样例 + 所属批次 + 租户名（superadmin 不被过滤，跨租户可读）
        sample_stmt = (
            select(
                PortalSampleRow,
                PortalSampleBatchRow.name.label("batch_name"),
                TenantRow.name.label("tenant_name"),
            )
            .outerjoin(
                PortalSampleBatchRow, PortalSampleBatchRow.id == PortalSampleRow.batch_id
            )
            .outerjoin(TenantRow, TenantRow.id == PortalSampleRow.tenant_id)
            .where(PortalSampleRow.id == sample_id)
        )
        row = (await session.execute(sample_stmt)).first()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Sample not found"
            )
        sample, batch_name, tenant_name = row

        # 该样例的全部反馈 + 评分人用户名
        fb_stmt = (
            select(SampleFeedbackRow, UserRow.username)
            .outerjoin(UserRow, UserRow.id == SampleFeedbackRow.rated_by)
            .where(SampleFeedbackRow.sample_id == sample_id)
            .order_by(SampleFeedbackRow.updated_at.desc())
        )
        fb_rows = (await session.execute(fb_stmt)).all()

    feedbacks: list[SampleFeedbackDetail] = []
    overalls: list[int] = []
    for fb, username in fb_rows:
        if fb.overall is not None:
            overalls.append(fb.overall)
        feedbacks.append(
            SampleFeedbackDetail(
                id=fb.id,
                rated_by=fb.rated_by,
                rated_by_username=username,
                overall=fb.overall,
                scores=fb.scores or {},
                comment=fb.comment,
                expected_answer=fb.expected_answer,
                created_at=fb.created_at.isoformat(),
                updated_at=fb.updated_at.isoformat(),
            )
        )

    avg_overall = _round(sum(overalls) / len(overalls)) if overalls else None

    return SampleFeedbackResponse(
        sample_id=sample.id,
        batch_id=sample.batch_id,
        batch_name=batch_name,
        tenant_id=sample.tenant_id,
        tenant_name=tenant_name,
        row_index=sample.row_index,
        question=sample.question,
        answer=sample.answer,
        extra=sample.extra or {},
        feedback_count=len(feedbacks),
        avg_overall=avg_overall,
        feedbacks=feedbacks,
    )


@router.get("/stats", response_model=FeedbackStatsResponse)
async def get_feedback_stats() -> FeedbackStatsResponse:
    """总览：按租户聚合平均分 + 覆盖率，并给全局汇总。

    覆盖率 = 已被评过的样例数 / 样例总数。只统计存在 portal 样例的租户。
    """
    # 按租户：样例总数 + 批次数
    samples_sq = (
        select(
            PortalSampleRow.tenant_id.label("tenant_id"),
            func.count(PortalSampleRow.id).label("sample_count"),
            func.count(func.distinct(PortalSampleRow.batch_id)).label("batch_count"),
        )
        .group_by(PortalSampleRow.tenant_id)
        .subquery()
    )
    # 按租户：已评样例数 + 反馈条数 + 平均总体分
    fb_sq = (
        select(
            SampleFeedbackRow.tenant_id.label("tenant_id"),
            func.count(func.distinct(SampleFeedbackRow.sample_id)).label("rated_count"),
            func.count(SampleFeedbackRow.id).label("feedback_count"),
            func.avg(SampleFeedbackRow.overall).label("avg_overall"),
        )
        .group_by(SampleFeedbackRow.tenant_id)
        .subquery()
    )

    stmt = (
        select(
            samples_sq.c.tenant_id,
            TenantRow.name.label("tenant_name"),
            samples_sq.c.sample_count,
            samples_sq.c.batch_count,
            fb_sq.c.rated_count,
            fb_sq.c.feedback_count,
            fb_sq.c.avg_overall,
        )
        .outerjoin(fb_sq, fb_sq.c.tenant_id == samples_sq.c.tenant_id)
        .outerjoin(TenantRow, TenantRow.id == samples_sq.c.tenant_id)
        .order_by(samples_sq.c.sample_count.desc())
    )

    async with async_session_factory() as session:
        rows = (await session.execute(stmt)).all()

    by_tenant: list[TenantFeedbackStat] = []
    total_samples = 0
    total_rated = 0
    total_feedbacks = 0
    total_batches = 0
    # 全局平均分按反馈条数加权（sum(avg*count)/sum(count)）
    weighted_sum = 0.0
    weighted_n = 0
    for (
        tid,
        tenant_name,
        sample_count,
        batch_count,
        rated_count,
        feedback_count,
        avg_overall,
    ) in rows:
        s_count = int(sample_count or 0)
        b_count = int(batch_count or 0)
        r_count = int(rated_count or 0)
        f_count = int(feedback_count or 0)
        total_samples += s_count
        total_batches += b_count
        total_rated += r_count
        total_feedbacks += f_count
        if avg_overall is not None and f_count > 0:
            weighted_sum += float(avg_overall) * f_count
            weighted_n += f_count
        by_tenant.append(
            TenantFeedbackStat(
                tenant_id=tid,
                tenant_name=tenant_name,
                batch_count=b_count,
                sample_count=s_count,
                rated_sample_count=r_count,
                feedback_count=f_count,
                avg_overall=_round(avg_overall),
                coverage=_coverage(r_count, s_count),
            )
        )

    global_avg = _round(weighted_sum / weighted_n) if weighted_n > 0 else None

    return FeedbackStatsResponse(
        total_tenants=len(by_tenant),
        total_batches=total_batches,
        total_samples=total_samples,
        total_rated_samples=total_rated,
        total_feedbacks=total_feedbacks,
        avg_overall=global_avg,
        coverage=_coverage(total_rated, total_samples),
        by_tenant=by_tenant,
    )
