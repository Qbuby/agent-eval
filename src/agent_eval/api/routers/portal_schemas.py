from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class BatchSummary(BaseModel):
    """一个上传批次的概要。"""

    id: str
    name: str
    row_count: int
    status: str
    uploaded_by: str | None = None
    created_at: datetime | None = None


class UploadBatchResponse(BaseModel):
    """xlsx 上传解析落库后返回的批次概要 + 识别到的列信息。"""

    batch: BatchSummary
    question_column: str | None = None
    answer_column: str | None = None
    extra_columns: list[str] = Field(default_factory=list)


class FeedbackPayload(BaseModel):
    """本人对单条样例已提交的反馈（随分页样例返回）。"""

    id: str
    overall: int | None = None
    scores: dict[str, Any] = Field(default_factory=dict)
    comment: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SampleItem(BaseModel):
    """分页返回的单条样例，含本人 feedback（若有）。"""

    id: str
    row_index: int
    question: str
    answer: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    feedback: FeedbackPayload | None = None


class SamplePage(BaseModel):
    """分页样例响应。"""

    batch_id: str
    page: int
    page_size: int
    total: int
    items: list[SampleItem] = Field(default_factory=list)


class SubmitFeedbackRequest(BaseModel):
    """提交/更新打分 + 意见。overall 1-5，scores 维度→分，comment 自由文本。"""

    overall: int | None = Field(default=None, ge=1, le=5)
    scores: dict[str, Any] = Field(default_factory=dict)
    comment: str | None = None


class PortalBatchProgress(BaseModel):
    """外部客户仪表盘：单个批次的评审进度（本人视角）。"""

    batch_id: str
    name: str
    sample_count: int  # 批次样例总数
    rated_count: int  # 本人已评样例数


class PortalStatsResponse(BaseModel):
    """外部客户仪表盘总览（当前租户 + 本人评审视角）。

    租户隔离由 db.py 监听器按 ContextVar 自动注入，本端点无需手写 where。
    评审进度按「本人」(rated_by == 当前用户) 统计，便于客户看自己的待办。
    """

    batch_count: int
    sample_count: int  # 全部批次样例总数
    rated_count: int  # 本人已评样例数
    coverage: float  # rated_count / sample_count，0-1
    avg_overall: float | None = None  # 本人评分的平均总体分
    by_batch: list[PortalBatchProgress] = Field(default_factory=list)
