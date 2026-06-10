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
