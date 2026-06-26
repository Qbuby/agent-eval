from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from agent_eval.api.dependencies import get_manager
from agent_eval.api.exporters import ExportColumn, build_export_response, validate_format
from agent_eval.api.schemas import AddCasesRequest, BatchDeleteRequest, TestCaseInput
from agent_eval.auth.dependencies import (
    ROLE_ADMIN,
    require_internal,
    require_role,
)
from agent_eval.data.benchmark_import import (
    iter_upload_rows,
    parse_conversations,
)
from agent_eval.data.dataset_manager import DatasetManager
from agent_eval.data.schemas import validate_and_parse
from agent_eval.governance.helpers import log_audit
from agent_eval.models.test_case import TestCase, TurnExpectation

# All case endpoints require an internal role (admin|user); external_customer -> 403.
router = APIRouter(tags=["cases"], dependencies=[Depends(require_internal())])


@router.get("/api/datasets/{name}/cases")
async def list_cases(
    name: str,
    split: str | None = Query(None),
    tag: list[str] | None = Query(None),
    as_of: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, description="按 name/description 模糊搜索"),
    mgr: DatasetManager = Depends(get_manager),
):
    as_of_dt = datetime.fromisoformat(as_of) if as_of else None
    try:
        cases = await mgr.load_cases(
            name, as_of=as_of_dt, splits=[split] if split else None,
            tags=tag,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LangSmith API error: {e}") from e

    if search:
        search_lower = search.lower()
        cases = [
            c for c in cases
            if search_lower in c.name.lower() or search_lower in (c.description or "").lower()
        ]

    total = len(cases)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = cases[start:end]

    return {
        "items": [c.model_dump(mode="json", exclude_none=True) for c in page_items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/api/datasets/{name}/cases")
async def add_cases(
    name: str,
    req: AddCasesRequest,
    mgr: DatasetManager = Depends(get_manager),
):
    raw_dicts = [c.model_dump(exclude_none=True) for c in req.cases]
    result = validate_and_parse(raw_dicts)
    if result.errors:
        raise HTTPException(status_code=422, detail=result.errors)

    if len(result.cases) == 1:
        ex_id = await mgr.add_case(name, result.cases[0], split=req.split)
        await log_audit("example", ex_id, "create", details={"dataset": name})
        return {"added": 1, "ids": [ex_id]}

    ids = await mgr.add_cases_batch(name, result.cases, split=req.split)
    await log_audit("example", name, "import", details={"count": len(ids), "ids": ids[:10]})
    return {"added": len(result.cases), "ids": ids}


@router.put("/api/cases/{example_id}")
async def update_case(
    example_id: str,
    req: TestCaseInput,
    mgr: DatasetManager = Depends(get_manager),
):
    raw = req.model_dump(exclude_none=True)
    result = validate_and_parse(raw)
    if result.errors:
        raise HTTPException(status_code=422, detail=result.errors)
    await mgr.update_case(example_id, result.cases[0])
    await log_audit("example", example_id, "update")
    return {"updated": example_id}


@router.delete("/api/cases/{example_id}", dependencies=[Depends(require_role(ROLE_ADMIN))])
async def delete_case(
    example_id: str,
    mgr: DatasetManager = Depends(get_manager),
):
    await mgr.delete_case(example_id)
    await log_audit("example", example_id, "delete")
    return {"deleted": example_id}


@router.post("/api/cases/batch-delete", dependencies=[Depends(require_role(ROLE_ADMIN))])
async def batch_delete_cases(
    req: BatchDeleteRequest,
    mgr: DatasetManager = Depends(get_manager),
):
    await mgr.delete_cases_batch(req.example_ids)
    await log_audit("example", "batch", "delete", details={"count": len(req.example_ids), "ids": req.example_ids[:10]})
    return {"deleted": len(req.example_ids)}


async def _parse_conversation_cases(
    content: bytes,
    filename: str,
    *,
    messages_column: str | None,
    goal_column: str | None,
) -> tuple[list[TestCase], int]:
    """文件字节 → (对话 TestCase 列表, 跳过行数)。preview 与 import 共用。

    自动适配三种布局（识别由 parse_conversations 完成，灵活匹配不同来源文件）：
    - chat 数组：消息列里是 [{"role","content"}, ...]
    - QA-turn 数组：消息列里是 [{"question","answer","expected_checkpoints"},...]
      （评测输出常见形态，如 turns 列）→ 展开成 user 轮 + 逐轮期望
    - 拍平多行：每行一个 turn，按 conversation_id 跨行聚合成一段对话
    问句/检查点 → 逐轮 criteria/expected_output，场景/目标列 → conversation_goal。
    无法构成任何轮次的行按跳过处理，不影响其余样例导入。
    """
    try:
        _, row_iter = iter_upload_rows(content, filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        conversations, skipped = parse_conversations(
            row_iter, messages_column=messages_column, goal_column=goal_column
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"文件解析失败：{e}") from e

    cases: list[TestCase] = []
    for i, conv in enumerate(conversations):
        first = next(
            (m["content"] for m in conv.input_messages if m.get("content")), ""
        )
        cases.append(TestCase(
            dataset_version="",  # 由调用方（import 端点）按 name 设定
            name=conv.name or f"conv-{i + 1}-{first[:30]}",
            description=conv.description,
            source="file_imported",
            input_messages=conv.input_messages,
            conversation_goal=conv.conversation_goal,
            turn_expectations=[TurnExpectation(**te) for te in conv.turn_expectations],
        ))
    return cases, skipped


@router.post("/api/datasets/{name}/cases/import-conversations/preview", dependencies=[Depends(require_role(ROLE_ADMIN))])
async def preview_conversations(
    name: str,
    file: UploadFile = File(...),
    messages_column: str | None = Query(None, description="手动指定消息列（覆盖自动识别）"),
    goal_column: str | None = Query(None, description="手动指定对话目标列（覆盖自动识别）"),
    mgr: DatasetManager = Depends(get_manager),
):
    """解析上传文件但不写库，返回解析结果预览 + 与现有同名样例的新增/更新比对。

    前端「两步式导入」第一步：用户确认解析结果（每段对话的轮数、首句、动作）
    后再调真正的导入端点。
    """
    content = await file.read()
    filename = file.filename or "unknown"
    cases, skipped = await _parse_conversation_cases(
        content, filename, messages_column=messages_column, goal_column=goal_column
    )

    # 与现有同名样例比对：命中→update，否则→new（按名 upsert 的预演）。
    try:
        existing = await mgr.load_cases(name)
    except Exception:
        existing = []
    existing_names = {c.name for c in existing}

    new_count = sum(1 for c in cases if c.name not in existing_names)
    updated_count = len(cases) - new_count

    samples = []
    for c in cases[:5]:
        user_msgs = [m for m in c.input_messages if m.get("role") == "user"]
        first_user = next((m["content"] for m in user_msgs if m.get("content")), "")
        samples.append({
            "name": c.name,
            "turns": len(user_msgs),
            "first_user": first_user[:80],
            "has_assistant": any(m.get("role") == "assistant" for m in c.input_messages),
            "checkpoints": sum(len(te.criteria or []) for te in c.turn_expectations),
            "goal": (c.conversation_goal or "")[:80],
            "action": "update" if c.name in existing_names else "new",
        })

    return {
        "total": len(cases),
        "new": new_count,
        "updated": updated_count,
        "skipped": skipped,
        "samples": samples,
    }


@router.post(
    "/api/datasets/{name}/cases/import-conversations",
    dependencies=[Depends(require_role(ROLE_ADMIN))],
)
async def import_conversations(
    name: str,
    file: UploadFile = File(...),
    split: str | None = Query(None),
    messages_column: str | None = Query(None, description="手动指定消息列（覆盖自动识别）"),
    goal_column: str | None = Query(None, description="手动指定对话目标列（覆盖自动识别）"),
    mgr: DatasetManager = Depends(get_manager),
):
    """从 CSV / JSON / JSONL / XLSX 文件批量导入多轮对话样例到数据集。

    按名 upsert：与现有同名样例命中则复用其 example_id（Langfuse
    create_dataset_item(id=) 天然 upsert → 按最新导入更新字段），否则新增。
    """
    content = await file.read()
    filename = file.filename or "unknown"
    cases, skipped = await _parse_conversation_cases(
        content, filename, messages_column=messages_column, goal_column=goal_column
    )

    if not cases:
        raise HTTPException(
            status_code=400,
            detail=f"未识别到任何多轮对话样例（文件为空或未匹配到问句/消息列；跳过 {skipped} 行）",
        )

    # 按名 upsert：命中现有同名样例就把 case.id 覆盖为已有 example_id（== 更新）。
    try:
        existing = await mgr.load_cases(name)
    except Exception:
        existing = []
    name_to_id = {c.name: c.id for c in existing}

    updated = 0
    for c in cases:
        c.dataset_version = name
        if c.name in name_to_id:
            c.id = name_to_id[c.name]
            updated += 1

    ids = await mgr.add_cases_batch(name, cases, split=split)
    added = len(cases) - updated
    await log_audit(
        "example", name, "import",
        details={"count": len(ids), "kind": "conversation", "added": added, "updated": updated},
    )
    return {"added": added, "updated": updated, "skipped": skipped, "ids": ids[:10]}


def _ascii_slug(name: str, fallback: str = "dataset") -> str:
    """数据集名 → ASCII 安全的文件名 base（Content-Disposition 不必走 RFC5987）。

    中文等非 ASCII 字符被剔除；若结果为空则用 fallback。浏览器另存仍能用，
    真实可读名由后端无法保证（名字可能全中文），这里只保证可下载。
    """
    slug = "".join(ch if ch.isalnum() or ch in "-_" else "" for ch in name)
    return slug[:40] or fallback


@router.get("/api/datasets/{name}/cases/export-conversations")
async def export_conversations(
    name: str,
    format: str = Query("xlsx"),
    split: str | None = Query(None),
    search: str | None = Query(None, description="按 name/description 模糊搜索"),
    mgr: DatasetManager = Depends(get_manager),
):
    """导出数据集的多轮对话样例为 csv / json / xlsx。

    与备选数据集 / 基准测试集的「导出」一致，复用 build_export_response。
    只导出多轮对话样例（多于 1 条消息，或带会话目标 / 逐轮期望）。
    """
    validate_format(format)
    try:
        cases = await mgr.load_cases(name)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"加载样例失败：{e}") from e

    if search:
        s = search.lower()
        cases = [
            c for c in cases
            if s in c.name.lower() or s in (c.description or "").lower()
        ]

    # 只保留多轮对话样例（与前端 isConversation 判定对齐）。
    def _is_conversation(c: TestCase) -> bool:
        if c.conversation_goal:
            return True
        if c.turn_expectations:
            return True
        return len(c.input_messages) > 1

    cases = [c for c in cases if _is_conversation(c)]

    rows = []
    for c in cases:
        user_turns = sum(1 for m in c.input_messages if m.get("role") == "user")
        rows.append({
            "id": c.id,
            "name": c.name,
            "description": c.description or "",
            "turns": user_turns,
            "conversation_goal": c.conversation_goal or "",
            "input_messages": [
                {"role": m.get("role"), "content": m.get("content")}
                for m in c.input_messages
            ],
            "turn_expectations": [
                {
                    "turn_index": te.turn_index,
                    "criteria": te.criteria,
                    "expected_output": te.expected_output,
                }
                for te in c.turn_expectations
            ],
            "source": c.source,
        })

    columns = [
        ExportColumn("id", "ID"),
        ExportColumn("name", "名称"),
        ExportColumn("description", "描述"),
        ExportColumn("turns", "轮数"),
        ExportColumn("conversation_goal", "会话目标"),
        ExportColumn("input_messages", "对话消息"),
        ExportColumn("turn_expectations", "逐轮期望"),
        ExportColumn("source", "来源"),
    ]
    return build_export_response(
        rows, columns, format, f"conversations_{_ascii_slug(name)}"
    )
