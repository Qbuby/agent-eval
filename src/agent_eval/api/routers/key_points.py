"""「提炼答案关键点」的 HTTP 接口。

提炼语义全在 ``agent_eval.evaluation.key_points_extractor`` 里，CLI
（``scripts/extract_key_points.py``）与本路由共用同一份实现；这里只做三件事：

1. 参数校验 + 把 ``ExtractionError`` 翻成 4xx；
2. 把当前请求的租户上下文显式交给后台任务（``asyncio.create_task`` 不继承
   contextvar 的后续修改，不带会落到内部 sentinel 租户）；
3. 暴露 job 的轮询与取消。

job 进度只驻内存（低频运维动作 + 提炼本身幂等，重跑即续上），所以状态查询在
多进程部署下只认本进程的 job——与 ``reply_generator`` 不同，这里没有 DB 兜底。

门禁与其他内部 router 一致：``require_internal``，external_customer 403。
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from agent_eval.auth.dependencies import require_internal
from agent_eval.data.langfuse_provider import (
    LangfuseDatasetProvider,
    build_langfuse_client,
)
from agent_eval.db_models.tenant_context import get_tenant_context
from agent_eval.evaluation import key_points_extractor as extractor
from agent_eval.evaluation.judge_clients import build_judge_client

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/key-points",
    tags=["key-points"],
    dependencies=[Depends(require_internal())],
)


class ExtractRequest(BaseModel):
    """一次提炼请求。

    ``target`` 决定数据来源与回写字段（candidate/benchmark 落本地表的
    key_points，multichat 落 Langfuse item 的 turn_expectations[].criteria）。

    ``case_ids`` 为空表示「该 target 下所有缺关键点的样例」；非空则只处理选中的
    这些（前端勾选范围）。两种情况都只命中「有答案且关键点为空」的行，重复触发
    不会覆盖已有关键点。
    """

    target: str
    case_ids: list[str] = Field(default_factory=list)
    # 多轮对话集的样例真身在 Langfuse，必须指明具体数据集名。
    dataset_name: str | None = None
    limit: int | None = Field(default=None, ge=1)
    provider_name: str = extractor.DEFAULT_PROVIDER_NAME
    model: str | None = None
    concurrency: int = Field(default=extractor.DEFAULT_CONCURRENCY, ge=1, le=20)


class JobStatusOut(BaseModel):
    """job 状态快照。前端轮询到 phase 落终态（done/failed/cancelled）即停。"""

    job_id: str
    phase: str
    total: int = 0
    done: int = 0
    extracted: int = 0
    failed: int = 0
    written: int = 0
    skipped_short: int = 0
    error: str | None = None
    targets: list[str] = Field(default_factory=list)
    active: bool = False


@router.post("/extract")
async def start_extract(req: ExtractRequest):
    """发起一次提炼。后台异步跑，立刻返回 job_id 供轮询。"""
    if req.target not in extractor.TARGETS:
        raise HTTPException(
            status_code=400,
            detail=f"target 必须是 {sorted(extractor.TARGETS)} 之一",
        )
    # 多轮集不带 dataset_name 会落到 extractor 里 CLI 时代的两个硬编码集名，
    # 那对 UI 触发是意外的写入范围，直接拦掉。
    if req.target == "multichat" and not req.dataset_name:
        raise HTTPException(
            status_code=400, detail="多轮对话集必须提供 dataset_name",
        )

    ctx = get_tenant_context()
    try:
        job_id = await extractor.start_extraction_job(
            targets=[req.target],
            limit=req.limit,
            provider_name=req.provider_name,
            model=req.model,
            concurrency=req.concurrency,
            case_ids=req.case_ids or None,
            dataset_names=[req.dataset_name] if req.dataset_name else None,
            tenant_ctx=ctx,
        )
    except extractor.ExtractionError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return {"job_id": job_id, "phase": "pending"}


@router.get("/jobs/{job_id}", response_model=JobStatusOut)
async def get_job(job_id: str):
    status = extractor.get_job_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="提炼任务不存在")
    return JobStatusOut(
        job_id=job_id,
        active=extractor.is_job_active(job_id),
        **status,
    )


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    """请求取消。正在飞的那条 LLM 调用跑完为止，后续单元直接放弃。"""
    if not extractor.request_cancel(job_id):
        raise HTTPException(status_code=404, detail="提炼任务不存在")
    return {"job_id": job_id, "cancelled": True}


class PendingCountOut(BaseModel):
    """待提炼计数。前端据此决定按钮是否可点、以及提示「将提炼 N 条」。"""

    target: str
    pending: int = 0
    # 有答案但太短、不值得提炼的条数，单独报出来避免用户困惑于「为什么少了几条」。
    skipped_short: int = 0


@router.get("/pending-count", response_model=PendingCountOut)
async def pending_count(
    target: str = Query(...),
    dataset_name: str | None = Query(default=None),
):
    """数「有答案且关键点为空」的样例条数。只读，不调 LLM、不写库。

    走的是与提炼同一套 collect_*，所以这里报的数就是点下按钮后实际会提炼的数，
    不会出现「显示 10 条实际跑 7 条」的偏差。
    """
    if target not in extractor.TARGETS:
        raise HTTPException(
            status_code=400,
            detail=f"target 必须是 {sorted(extractor.TARGETS)} 之一",
        )
    if target == "multichat" and not dataset_name:
        raise HTTPException(
            status_code=400, detail="多轮对话集必须提供 dataset_name",
        )

    try:
        if target == "candidate":
            units = await extractor.collect_candidate()
        elif target == "benchmark":
            units = await extractor.collect_benchmark()
        else:
            provider = LangfuseDatasetProvider(await build_langfuse_client())
            units, _ = await extractor.collect_multichat(
                provider, dataset_names=[dataset_name]
            )
    except extractor.ExtractionError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001 - Langfuse 不可达时给 502 而非 500
        logger.warning("pending-count 采集失败 target=%s: %s", target, e)
        raise HTTPException(
            status_code=502, detail=f"读取待提炼样例失败：{type(e).__name__}: {e}"
        ) from e

    short = len([u for u in units if extractor.too_short(u.answer)])
    return PendingCountOut(
        target=target, pending=len(units) - short, skipped_short=short
    )


class ExtractOneRequest(BaseModel):
    """单条即时提炼（编辑弹窗里的「AI 提炼」按钮）。

    只回结果不写库——用户要在弹窗里看过、能改，再随表单一起保存。故这里不带
    target/case_id，纯粹是「给一段答案，返回关键点」。
    """

    answer: str
    question: str = ""
    provider_name: str = extractor.DEFAULT_PROVIDER_NAME
    model: str | None = None


class ExtractOneOut(BaseModel):
    points: list[str] = Field(default_factory=list)


@router.post("/extract-one", response_model=ExtractOneOut)
async def extract_one_case(req: ExtractOneRequest):
    """同步提炼一条并直接返回关键点。前端拿到后填进表单，不落库。"""
    answer = (req.answer or "").strip()
    if not answer:
        raise HTTPException(status_code=400, detail="答案为空，无从提炼")
    if extractor.too_short(answer):
        raise HTTPException(
            status_code=400,
            detail=f"答案短于 {extractor.MIN_ANSWER_CHARS} 字，提炼无信息增益",
        )

    try:
        provider_row = await extractor.load_provider_row(req.provider_name)
    except extractor.ExtractionError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # 单条走同步：sem 容量 1，超时沿用批量的 120s（judge_clients 自带重试）。
    sem = asyncio.Semaphore(1)
    async with build_judge_client(
        provider_row, model=req.model, max_tokens=extractor.MAX_TOKENS, timeout=120.0
    ) as client:
        points, error = await extractor.extract_one(
            client, sem, req.question, answer
        )

    if error:
        # 上游报错/解析失败都是「这次没成」，让前端原样展示原因供重试。
        raise HTTPException(status_code=502, detail=f"提炼失败：{error}")
    return ExtractOneOut(points=points)
