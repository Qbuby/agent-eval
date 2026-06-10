"""内部反馈展示（feedback-api 摊）。

外部客户在 portal 对样例的手动打分 + 意见，回流到内部入口的这个模块给
内部 admin 查看。全部端点 ``require_role(ROLE_ADMIN)``：内部 admin 登录态是
superadmin，db.py 的读监听器对 superadmin **旁路过滤**，所以这里直连
``async_session_factory()`` 查 TenantMixin 表（batches/samples/feedbacks）能
**跨租户**看到全部数据，无需手写 ``.where(tenant_id==...)``。

可选 ``tenant_id`` 过滤是「想只看某租户」时由调用方显式追加的 where —— 因为
superadmin 默认不被过滤，不显式加就是全租户。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select

from agent_eval.auth.dependencies import ROLE_ADMIN, require_role
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import (
    PortalSampleBatchRow,
    PortalSampleRow,
    SampleFeedbackRow,
    TenantRow,
    UserRow,
)

router = APIRouter(
    prefix="/api/feedback",
    tags=["feedback-review"],
    dependencies=[Depends(require_role(ROLE_ADMIN))],
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


class SampleFeedbackDetail(BaseModel):
    """单条客户反馈明细。"""

    id: uuid.UUID
    rated_by: uuid.UUID | None = None
    rated_by_username: str | None = None
    overall: int | None = None
    scores: dict
    comment: str | None = None
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
