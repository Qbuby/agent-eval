from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select

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
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import ConversationCategoryRow
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
    category: str | None = Query(None, description="按受管类别名精确过滤（多轮对话集）"),
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

    # 受管类别过滤：与 search/tag 同构（全量 load + 内存 filter）。category 存在
    # case.category（→ Langfuse item metadata["category"]，见 converter）。
    if category:
        cases = [c for c in cases if (c.category or "") == category]

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


@router.get("/api/cases/{example_id}")
async def get_case(
    example_id: str,
    mgr: DatasetManager = Depends(get_manager),
):
    """按 id 取单条样例的完整详情（读操作，内部角色即可，无需 admin）。"""
    try:
        case = await mgr.get_case(example_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"未找到样例 {example_id}：{e}") from e
    return case.model_dump(mode="json", exclude_none=True)


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


@router.delete("/api/cases/{example_id}")
async def delete_case(
    example_id: str,
    mgr: DatasetManager = Depends(get_manager),
):
    await mgr.delete_case(example_id)
    await log_audit("example", example_id, "delete")
    return {"deleted": example_id}


@router.post("/api/cases/batch-delete")
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
    category: str | None = None,
    column_map: dict[str, str] | None = None,
) -> tuple[list[TestCase], int]:
    """文件字节 → (对话 TestCase 列表, 跳过行数)。preview 与 import 共用。

    自动适配三种布局（识别由 parse_conversations 完成，灵活匹配不同来源文件）：
    - chat 数组：消息列里是 [{"role","content"}, ...]
    - QA-turn 数组：消息列里是 [{"question","answer","expected_checkpoints"},...]
      （评测输出常见形态，如 turns 列）→ 展开成 user 轮 + 逐轮期望
    - 拍平多行：每行一个 turn，按 conversation_id 跨行聚合成一段对话
    问句/检查点 → 逐轮 criteria/expected_output，场景/目标列 → conversation_goal。
    无法构成任何轮次的行按跳过处理，不影响其余样例导入。

    column_map（可选）：拍平布局下语义字段 → 源列名的显式映射，让用户手动指定
    question / answer / expected_output / criteria / conversation_id / turn_no /
    goal / name 各对应哪列，覆盖别名自动识别。expected_output 是导入侧唯一能带
    入「期望答案」的路径。
    """
    try:
        _, row_iter = iter_upload_rows(content, filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return _cases_from_conversation_rows(
        row_iter, messages_column=messages_column, goal_column=goal_column,
        category=category, column_map=column_map, source="file_imported",
    )


def _cases_from_conversation_rows(
    rows,
    *,
    messages_column: str | None,
    goal_column: str | None,
    category: str | None = None,
    column_map: dict[str, str] | None = None,
    source: str = "file_imported",
) -> tuple[list[TestCase], int]:
    """行迭代器 → (对话 TestCase 列表, 跳过行数)。

    与文件格式解耦——上传文件走 iter_upload_rows 产出行，飞书多维表格走
    records_to_rows 产出同形行（{列名: 值} 的 dict），两者共用此下游：同一套
    parse_conversations 三布局识别 + column_map + TestCase 构造。
    """
    try:
        conversations, skipped = parse_conversations(
            rows, messages_column=messages_column, goal_column=goal_column,
            column_map=column_map,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"解析失败：{e}") from e

    cases: list[TestCase] = []
    for i, conv in enumerate(conversations):
        first = next(
            (m["content"] for m in conv.input_messages if m.get("content")), ""
        )
        cases.append(TestCase(
            dataset_version="",  # 由调用方（import 端点）按 name 设定
            name=conv.name or f"conv-{i + 1}-{first[:30]}",
            description=conv.description,
            source=source,
            input_messages=conv.input_messages,
            conversation_goal=conv.conversation_goal,
            turn_expectations=[TurnExpectation(**te) for te in conv.turn_expectations],
            category=category or None,
        ))
    return cases, skipped


def _parse_column_map(raw: str | None) -> dict[str, str] | None:
    """把前端传来的 column_map JSON 字符串解析成 dict。空/非法 → None（回退
    别名自动识别）。只保留值为非空字符串的项，避免把空映射当成显式指定。"""
    if not raw:
        return None
    import json
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(status_code=400, detail="column_map 不是合法 JSON") from None
    if not isinstance(obj, dict):
        raise HTTPException(status_code=400, detail="column_map 必须是对象")
    cleaned = {
        str(k): str(v).strip()
        for k, v in obj.items()
        if v not in (None, "") and str(v).strip()
    }
    return cleaned or None


@router.post(
    "/api/datasets/{name}/cases/import-conversations/inspect",
)
async def inspect_conversation_file(
    name: str,
    file: UploadFile = File(...),
):
    """解析上传文件的列结构：返回列头 + 每列样例值 + 自动建议的字段映射。

    「三步式导入」第一步（字段映射）用它——前端据此渲染「语义字段 → 源列」
    的下拉，用户可在自动建议基础上手动纠正后再预览。仅读表头与前几行，不写库。

    行内已带对话数组（messages/turns 列，布局 A/B）的文件不需要列映射：这种
    情况返回空 columns + is_structured=true，前端可直接跳到预览。
    """
    from agent_eval.data.benchmark_import import (
        collect_sample_values,
        suggest_conversation_column_map,
    )

    content = await file.read()
    filename = file.filename or "unknown"
    try:
        headers, row_iter = iter_upload_rows(content, filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # 只物化前若干行做样例（避免大文件全量读入）。
    sample_rows: list[dict] = []
    for i, row in enumerate(row_iter):
        if i >= 20:
            break
        if isinstance(row, dict):
            sample_rows.append(row)

    # 行内数组布局（A/B）判定：任一样例行的某个消息列别名能解析成非空 list。
    from agent_eval.data.benchmark_import import _MESSAGES_COLUMN_ALIASES, _as_list
    is_structured = False
    lower_alias = {a.lower() for a in _MESSAGES_COLUMN_ALIASES}
    for row in sample_rows:
        for k, v in row.items():
            if str(k).lower().strip() in lower_alias and _as_list(v) is not None:
                is_structured = True
                break
        if is_structured:
            break

    samples = collect_sample_values(sample_rows, headers, limit=3)
    suggested = suggest_conversation_column_map(headers)
    return {
        "columns": [h for h in headers if h],
        "samples": samples,
        "suggested": suggested,
        "is_structured": is_structured,
    }


@router.post("/api/datasets/{name}/cases/import-conversations/preview")
async def preview_conversations(
    name: str,
    file: UploadFile = File(...),
    messages_column: str | None = Query(None, description="手动指定消息列（覆盖自动识别）"),
    goal_column: str | None = Query(None, description="手动指定对话目标列（覆盖自动识别）"),
    category: str | None = Query(None, description="为整批导入样例统一指定类别"),
    column_map: str | None = Query(None, description="语义字段→源列名的 JSON 映射（覆盖别名识别）"),
    mgr: DatasetManager = Depends(get_manager),
):
    """解析上传文件但不写库，返回解析结果预览 + 与现有同名样例的新增/更新比对。

    前端「两步式导入」第一步：用户确认解析结果（每段对话的轮数、首句、动作）
    后再调真正的导入端点。
    """
    content = await file.read()
    filename = file.filename or "unknown"
    cases, skipped = await _parse_conversation_cases(
        content, filename, messages_column=messages_column, goal_column=goal_column,
        category=category, column_map=_parse_column_map(column_map),
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
            # 带期望答案（expected_output）的轮数：让用户在预览里确认「期望答案」
            # 列有没有被正确映射进来。
            "expected_answers": sum(
                1 for te in c.turn_expectations if te.expected_output
            ),
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
)
async def import_conversations(
    name: str,
    file: UploadFile = File(...),
    split: str | None = Query(None),
    messages_column: str | None = Query(None, description="手动指定消息列（覆盖自动识别）"),
    goal_column: str | None = Query(None, description="手动指定对话目标列（覆盖自动识别）"),
    category: str | None = Query(None, description="为整批导入样例统一指定类别"),
    column_map: str | None = Query(None, description="语义字段→源列名的 JSON 映射（覆盖别名识别）"),
    mgr: DatasetManager = Depends(get_manager),
):
    """从 CSV / JSON / JSONL / XLSX 文件批量导入多轮对话样例到数据集。

    按名 upsert：与现有同名样例命中则复用其 example_id（Langfuse
    create_dataset_item(id=) 天然 upsert → 按最新导入更新字段），否则新增。

    column_map（可选，JSON 串）：与 preview 一致的语义字段→源列名映射，确保
    「用户在映射步骤确认的字段」和真正落库的解析口径完全一致。
    """
    content = await file.read()
    filename = file.filename or "unknown"
    cases, skipped = await _parse_conversation_cases(
        content, filename, messages_column=messages_column, goal_column=goal_column,
        category=category, column_map=_parse_column_map(column_map),
    )

    if not cases:
        raise HTTPException(
            status_code=400,
            detail=f"未识别到任何多轮对话样例（文件为空或未匹配到问句/消息列；跳过 {skipped} 行）",
        )
    return await _upsert_conversation_cases(
        mgr, name, cases, skipped=skipped, split=split, kind="conversation",
    )


async def _upsert_conversation_cases(
    mgr: DatasetManager,
    name: str,
    cases: list[TestCase],
    *,
    skipped: int,
    split: str | None,
    kind: str,
) -> dict:
    """按名 upsert 一批对话样例并落库。文件导入与 Bitable 导入共用。

    命中现有同名样例则复用其 example_id（Langfuse create_dataset_item(id=)
    天然 upsert → 更新字段），否则新增。返回 {added, updated, skipped, ids}。
    """
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
        details={"count": len(ids), "kind": kind, "added": added, "updated": updated},
    )
    return {"added": added, "updated": updated, "skipped": skipped, "ids": ids[:10]}


class ImportBitableRequest(BaseModel):
    app_token: str
    table_id: str
    split: str | None = None
    category: str | None = None
    # 语义字段 → Bitable 列名 的映射（覆盖别名自动识别）。多维表格列名即行 dict 的键。
    column_map: dict[str, str] | None = None
    messages_column: str | None = None
    goal_column: str | None = None


async def _fetch_bitable_rows(app_token: str, table_id: str, user) -> list[dict]:
    """用当前用户 OAuth token 拉取整表记录并归一成行 dict 列表。

    无有效 token → 428（引导去飞书授权）；权限/读取失败 → 由 BitableError
    冒泡到端点转 400。返回的行是 {列名: 归一值}，可直接喂对话解析下游。
    """
    if user is None:
        raise HTTPException(status_code=400, detail="需要登录用户以使用其飞书授权访问多维表格")
    from agent_eval.feishu.bitable import BitableClient, BitableError
    from agent_eval.feishu.bitable import records_to_rows
    from agent_eval.feishu.oauth import get_valid_user_token

    token = await get_valid_user_token(user.id)
    if not token:
        raise HTTPException(
            status_code=428,
            detail="尚未完成飞书多维表格授权或授权已过期，请在飞书里点击授权链接后重试",
        )
    try:
        records = await BitableClient(token).list_all_records(app_token, table_id)
    except BitableError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return records_to_rows(records)


@router.post(
    "/api/datasets/{name}/cases/import-bitable/inspect",
    dependencies=[Depends(require_role(ROLE_ADMIN))],
)
async def inspect_bitable(
    name: str,
    req: ImportBitableRequest,
    user=Depends(require_role(ROLE_ADMIN)),
):
    """拉取多维表格首若干行，返回列头 + 每列样例 + 建议映射（对齐文件 inspect 契约）。"""
    from agent_eval.data.benchmark_import import (
        collect_sample_values,
        suggest_conversation_column_map,
    )

    rows = await _fetch_bitable_rows(req.app_token, req.table_id, user)
    sample_rows = [r for r in rows[:20] if isinstance(r, dict)]
    headers: list[str] = []
    for r in sample_rows:
        for k in r.keys():
            if k != "_record_id" and k not in headers:
                headers.append(k)
    samples = collect_sample_values(sample_rows, headers, limit=3)
    suggested = suggest_conversation_column_map(headers)
    return {
        "columns": headers,
        "samples": samples,
        "suggested": suggested,
        "total_rows": len(rows),
    }


@router.post(
    "/api/datasets/{name}/cases/import-bitable",
    dependencies=[Depends(require_role(ROLE_ADMIN))],
)
async def import_bitable(
    name: str,
    req: ImportBitableRequest,
    mgr: DatasetManager = Depends(get_manager),
    user=Depends(require_role(ROLE_ADMIN)),
):
    """从飞书多维表格批量导入多轮对话样例到数据集。

    行来源换成 Bitable 记录（用当前用户 OAuth token 访问其私人表），
    下游复用与文件导入完全相同的 parse_conversations 三布局识别 +
    column_map + TestCase 构造 + 按名 upsert。
    """
    rows = await _fetch_bitable_rows(req.app_token, req.table_id, user)
    cases, skipped = _cases_from_conversation_rows(
        rows,
        messages_column=req.messages_column,
        goal_column=req.goal_column,
        category=req.category,
        column_map=req.column_map or None,
        source="bitable_imported",
    )
    if not cases:
        raise HTTPException(
            status_code=400,
            detail=f"未从多维表格识别到任何多轮对话样例（未匹配到问句/消息列；跳过 {skipped} 行）",
        )
    return await _upsert_conversation_cases(
        mgr, name, cases, skipped=skipped, split=req.split, kind="conversation_bitable",
    )


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


# ─────────────────────────────────────────────────────────────────────────
# 多轮对话集的受管类别（对齐基准测试集 CategoryRow CRUD，作用域 = dataset_name）。
#
# 实体存 Postgres（conversation_categories），样例→类别归属以类别名字符串存进
# Langfuse item 的 metadata["category"]（见 converter）。故：
#   * 删除前先 load_cases 统计该类别下样例数，>0 拒删（409），与基准一致。
#   * 重命名时把旧名样例的 metadata.category 批量改写成新名（无外键级联，手动同步）。
# 这两步都要遍历 Langfuse items，成本与现有 list/导出同量级（本就全量 load）。
# ─────────────────────────────────────────────────────────────────────────


class CreateConvCategoryRequest(BaseModel):
    name: str
    description: str = ""


class UpdateConvCategoryRequest(BaseModel):
    name: str | None = None
    description: str | None = None


def _conv_cat_dict(row: ConversationCategoryRow) -> dict:
    return {
        "id": str(row.id),
        "name": row.name,
        "description": row.description,
        "created_at": row.created_at,
    }


@router.get("/api/datasets/{name}/categories")
async def list_conv_categories(name: str):
    """列出某对话集下的全部受管类别（按 name 排序）。"""
    async with async_session_factory() as session:
        result = await session.execute(
            select(ConversationCategoryRow)
            .where(ConversationCategoryRow.dataset_name == name)
            .order_by(ConversationCategoryRow.name)
        )
        return [_conv_cat_dict(r) for r in result.scalars().all()]


@router.post("/api/datasets/{name}/categories")
async def create_conv_category(name: str, req: CreateConvCategoryRequest):
    """新建类别。幂等：同 (dataset_name, name) 已存在则直接返回现有行。"""
    cat_name = (req.name or "").strip()
    if not cat_name:
        raise HTTPException(status_code=400, detail="类别名不能为空")
    async with async_session_factory() as session:
        existing = await session.execute(
            select(ConversationCategoryRow).where(
                ConversationCategoryRow.dataset_name == name,
                ConversationCategoryRow.name == cat_name,
            )
        )
        row = existing.scalar_one_or_none()
        if row is not None:
            return _conv_cat_dict(row)
        row = ConversationCategoryRow(
            dataset_name=name, name=cat_name, description=req.description or None
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
    await log_audit("conversation_category", str(row.id), "create", details={"dataset": name, "name": cat_name})
    return _conv_cat_dict(row)


@router.put("/api/datasets/categories/{category_id}")
async def update_conv_category(
    category_id: str,
    req: UpdateConvCategoryRequest,
    mgr: DatasetManager = Depends(get_manager),
):
    """重命名 / 改描述。重命名时把该类别下样例的 metadata.category 批量同步成新名。"""
    async with async_session_factory() as session:
        result = await session.execute(
            select(ConversationCategoryRow).where(ConversationCategoryRow.id == category_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Category not found")

        old_name = row.name
        dataset_name = row.dataset_name
        new_name = (req.name or "").strip() if req.name is not None else None

        if new_name and new_name != old_name:
            # 唯一性预检（DB 也有唯一约束兜底，这里给出友好报错）。
            dup = await session.execute(
                select(ConversationCategoryRow).where(
                    ConversationCategoryRow.dataset_name == dataset_name,
                    ConversationCategoryRow.name == new_name,
                )
            )
            if dup.scalar_one_or_none() is not None:
                raise HTTPException(status_code=409, detail=f"类别名「{new_name}」已存在")
            row.name = new_name
        if req.description is not None:
            row.description = req.description or None
        await session.commit()
        await session.refresh(row)
        renamed = bool(new_name and new_name != old_name)

    # 把旧名样例的 metadata.category 批量改写为新名（无外键，手动同步）。
    synced = 0
    if renamed:
        try:
            cases = await mgr.load_cases(dataset_name)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"读取样例失败，类别已改名但样例未同步：{e}") from e
        for c in cases:
            if c.category == old_name:
                c.category = new_name
                try:
                    await mgr.update_case(c.id, c)
                    synced += 1
                except Exception:
                    # 单条同步失败不阻断整体；返回 synced 计数供前端提示。
                    pass
    await log_audit(
        "conversation_category", category_id, "update",
        details={"dataset": dataset_name, "renamed": renamed, "synced_cases": synced},
    )
    return {**_conv_cat_dict(row), "synced_cases": synced}


@router.delete("/api/datasets/categories/{category_id}")
async def delete_conv_category(category_id: str, mgr: DatasetManager = Depends(get_manager)):
    """删除类别。保护性拒删：该类别下仍有样例则 409，要求先移除/改类。"""
    async with async_session_factory() as session:
        result = await session.execute(
            select(ConversationCategoryRow).where(ConversationCategoryRow.id == category_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Category not found")
        dataset_name = row.dataset_name
        cat_name = row.name

    # 引用保护：统计该类别下的样例（按 metadata.category 名匹配）。
    try:
        cases = await mgr.load_cases(dataset_name)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"读取样例失败：{e}") from e
    in_use = sum(1 for c in cases if c.category == cat_name)
    if in_use > 0:
        raise HTTPException(
            status_code=409,
            detail=f"无法删除：类别「{cat_name}」下还有 {in_use} 条样例，请先移除或改类。",
        )

    async with async_session_factory() as session:
        result = await session.execute(
            select(ConversationCategoryRow).where(ConversationCategoryRow.id == category_id)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            await session.delete(row)
            await session.commit()
    await log_audit("conversation_category", category_id, "delete", details={"dataset": dataset_name, "name": cat_name})
    return {"deleted": category_id}
