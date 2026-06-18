"""
Benchmark import service - 支持按 category schema 配置进行文件导入和字段映射。

Category 的 schema_config 格式:
{
    "id_prefix": "EXT-EC",
    "id_digits": 3,
    "columns": [
        {"name": "input", "type": "mapped", "required": true, "description": "输入文本"},
        {"name": "expected_output", "type": "mapped", "required": false},
        {"name": "language", "type": "auto_detect", "source": "input"},
        {"name": "error_code", "type": "mapped", "required": false},
        {"name": "truck_model", "type": "auto_extract_model", "source": "input"},
        ...
    ]
}

字段类型:
- "mapped": 从源文件映射（用户指定或自动匹配列名）
- "auto_detect": 自动检测语言（基于中文字符判断）
- "auto_extract_model": 自动从文本中提取车型
- "fixed": 固定值（value 字段指定）
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import openpyxl

from agent_eval.data._utils import normalize_messages

VEHICLE_MODEL_PATTERN = re.compile(
    r"(RPL[A-Z0-9]+|CPD[A-Z0-9]+|CQD[A-Z0-9]+|EPT[A-Z0-9\-]+|EXP[A-Z0-9]+|"
    r"EPL[A-Z0-9]+|WPL[A-Z0-9]+|DS\d[A-Z0-9]*|KPL[A-Z0-9]+|F\d{4}[A-Z0-9]*)",
    re.IGNORECASE,
)

COLUMN_ALIASES: dict[str, list[str]] = {
    "question": [
        "question", "问题", "q", "query", "input", "输入",
        "prompt", "用户问题", "提问", "用户输入", "问句", "题目", "instruction",
    ],
    "input": ["input", "输入", "问题", "question", "请求体"],
    "expected_output": [
        "expected_output", "expected", "预期", "预期结果", "答案", "期望输出", "正确答案",
    ],
    "expected_answer_url": ["expected_answer_url", "url", "expected_keywords", "链接"],
    "expected_answer": [
        "expected_answer", "answer", "答案", "参考答案", "reference_answer",
        "标准答案", "参考回答", "gold", "gold_answer", "ground_truth", "label",
        "期望答案", "正确答案", "reference", "golden", "golden_answer",
    ],
    "reference_response": ["reference_response", "response", "回答", "回复"],
    "reference_answer": [
        "reference_answer", "answer", "答案", "参考答案",
        "标准答案", "参考回答", "gold", "gold_answer", "golden", "golden_answer",
        "ground_truth", "期望答案", "正确答案", "reference",
        "expected_answer", "expected_output",
    ],
    "error_code": ["error_code", "errorcode", "故障码", "错误码", "ErrorCode"],
    "truck_model": ["truck_model", "truckmodel", "车型", "model", "TruckModel"],
    "success": ["success", "成功", "是否成功", "result"],
    "annotation": ["annotation", "备注", "注释", "comment"],
    "key_points": ["key_points", "关键点", "要点"],
    "negative_points": ["negative_points", "反向关键点", "负面要点"],
    "tags": ["tags", "标签", "tag"],
    "difficulty": ["difficulty", "难度"],
}


def detect_language(text: str) -> str:
    if not text:
        return "en"
    for ch in text:
        if "一" <= ch <= "鿿":
            return "zh"
    return "en"


def extract_truck_model(text: str) -> str | None:
    if not text:
        return None
    match = VEHICLE_MODEL_PATTERN.search(text)
    return match.group(1) if match else None


@dataclass
class ImportResult:
    success: bool
    records_imported: int = 0
    records_pending: int = 0
    total_records: int = 0
    field_mapping_used: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def parse_upload_file(content: bytes, filename: str) -> tuple[list[str], list[dict[str, Any]]]:
    """解析上传文件，返回 (headers, rows_as_dicts)"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "csv":
        return _parse_csv(content)
    elif ext in ("json", "jsonl"):
        return _parse_json(content)
    elif ext in ("xlsx", "xls"):
        return _parse_xlsx(content)
    else:
        raise ValueError(f"Unsupported file type: {ext}")


def _parse_csv(content: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    rows = [dict(row) for row in reader]
    return list(headers), rows


def _parse_json(content: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    text = content.decode("utf-8-sig")

    # 先尝试整体解析（标准 JSON）
    try:
        data = json.loads(text)
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict) and "test_cases" in data:
            items = data["test_cases"]
        elif isinstance(data, dict):
            items = [data]
        else:
            items = []
    except json.JSONDecodeError:
        # 回退到 JSONL 逐行解析
        lines = text.strip().splitlines()
        items = [json.loads(line) for line in lines if line.strip()]

    if not items:
        return [], []
    headers = list(items[0].keys())
    return headers, items


def _parse_xlsx(content: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    ws = wb.active
    raw_headers = [str(cell.value or "").strip() for cell in ws[1]]
    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not any(cell is not None for cell in row):
            continue
        rows.append(dict(zip(raw_headers, row)))
    return raw_headers, rows


# ── Streaming API ─────────────────────────────────────────────────────────
# parse_upload_file (above) materializes every row into a list — fine for
# small files / preview, but OOMs on large uploads. iter_upload_rows returns
# (headers, lazy_row_iterator) so the import endpoint can consume row-by-row
# and commit in batches without holding the whole file in memory.

def iter_upload_rows(
    content: bytes, filename: str
) -> tuple[list[str], Iterator[dict[str, Any]]]:
    """Return (headers, row_iterator). The iterator yields one dict per row,
    lazily where the format allows (xlsx read-only, csv DictReader, jsonl).
    Standard (non-line) JSON must be fully parsed, so it falls back to a list
    iterator — JSON's structure makes true streaming impractical."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "csv":
        return _iter_csv(content)
    elif ext == "jsonl":
        return _iter_jsonl(content)
    elif ext == "json":
        # Standard JSON can't be streamed structurally; reuse the eager parser
        # but hand back an iterator so callers have one code path.
        headers, rows = _parse_json(content)
        return headers, iter(rows)
    elif ext in ("xlsx", "xls"):
        return _iter_xlsx(content)
    else:
        raise ValueError(f"Unsupported file type: {ext}")


def _iter_csv(content: bytes) -> tuple[list[str], Iterator[dict[str, Any]]]:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    headers = list(reader.fieldnames or [])

    def gen() -> Iterator[dict[str, Any]]:
        for row in reader:
            yield dict(row)

    return headers, gen()


def _iter_jsonl(content: bytes) -> tuple[list[str], Iterator[dict[str, Any]]]:
    text = content.decode("utf-8-sig")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    headers: list[str] = []
    if lines:
        try:
            headers = list(json.loads(lines[0]).keys())
        except (json.JSONDecodeError, AttributeError):
            headers = []

    def gen() -> Iterator[dict[str, Any]]:
        for ln in lines:
            try:
                obj = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj

    return headers, gen()


def _iter_xlsx(content: bytes) -> tuple[list[str], Iterator[dict[str, Any]]]:
    # read_only=True streams rows without loading the whole sheet into memory.
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    ws = wb.active
    row_iter = ws.iter_rows(values_only=True)
    try:
        header_row = next(row_iter)
    except StopIteration:
        wb.close()
        return [], iter(())
    raw_headers = [str(c or "").strip() for c in header_row]

    def gen() -> Iterator[dict[str, Any]]:
        try:
            for row in row_iter:
                if not any(cell is not None for cell in row):
                    continue
                yield dict(zip(raw_headers, row))
        finally:
            wb.close()

    return raw_headers, gen()


def auto_detect_field_mapping(headers: list[str]) -> dict[str, str | None]:
    """Suggest which source column maps to question / reference_answer,
    independent of any category schema. Tries exact (case-insensitive) match
    first, then the alias table. Returns {"question": <col or None>,
    "reference_answer": <col or None>}.

    The answer column avoids reusing the one already picked for question (e.g.
    when a sloppy header matches both alias lists)."""
    source_lower = {h.lower().strip(): h for h in headers if h}

    def match(target: str) -> str | None:
        if target in source_lower:
            return source_lower[target]
        for alias in COLUMN_ALIASES.get(target, []):
            if alias.lower() in source_lower:
                return source_lower[alias.lower()]
        return None

    q = match("question")
    a = match("reference_answer")
    if a is not None and a == q:
        # Don't map the same column to both; let the answer fall through.
        a = None
    return {"question": q, "reference_answer": a}


def resolve_question_answer(
    row: dict[str, Any],
    *,
    question_column: str | None = None,
    answer_column: str | None = None,
    schema_columns: list[dict] | None = None,
    field_mapping: dict[str, str] | None = None,
) -> tuple[str | None, str | None]:
    """Extract (question, reference_answer) from a row.

    Precedence: explicit column override (from the UI) > category schema
    mapping > alias/hardcoded fallback. Override wins so the user can always
    correct a wrong auto-detection."""
    schema_columns = schema_columns or []
    field_mapping = field_mapping or {}

    # Question
    question: str | None = None
    if question_column and question_column in row and row[question_column] not in (None, ""):
        question = str(row[question_column]).strip()
    else:
        question = get_question_from_row(row, schema_columns, field_mapping)

    # Answer
    answer: str | None = None
    if answer_column and answer_column in row and row[answer_column] not in (None, ""):
        answer = str(row[answer_column]).strip()
    else:
        answer = get_answer_from_row(row, schema_columns, field_mapping)

    return (question or None), (answer or None)


# 多轮对话导入：一行 = 一个对话样例，对话消息列里放消息数组。
_MESSAGES_COLUMN_ALIASES = (
    "messages", "input_messages", "conversation", "dialog", "dialogue",
    "对话", "消息", "会话", "chat", "chat_history", "turns",
)
_GOAL_COLUMN_ALIASES = (
    "conversation_goal", "goal", "对话目标", "目标", "session_goal",
)


def resolve_messages(
    row: dict[str, Any],
    *,
    messages_column: str | None = None,
) -> list[dict[str, str]] | None:
    """从一行里抽取多轮对话消息列表。

    一行代表一个完整对话样例，消息列里可以是：
    - 已解析的 list（JSON/JSONL 文件天然如此）
    - JSON 字符串（CSV/XLSX 单元格里塞的 JSON 数组）
    显式列名优先，否则按别名表自动识别。识别不到返回 None（按单轮处理）。
    """
    candidates: list[str] = []
    if messages_column:
        candidates.append(messages_column)
    candidates.extend(a for a in _MESSAGES_COLUMN_ALIASES if a not in candidates)

    lower_map = {str(k).lower().strip(): k for k in row}
    for cand in candidates:
        key = cand if cand in row else lower_map.get(cand.lower())
        if key is None:
            continue
        raw = row[key]
        if raw in (None, ""):
            continue
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                # 不是 JSON 数组，当作单条用户输入，交回单轮处理
                return None
        if isinstance(raw, list) and raw:
            msgs = normalize_messages(raw)
            return msgs or None
    return None


def resolve_conversation_goal(
    row: dict[str, Any],
    *,
    goal_column: str | None = None,
) -> str | None:
    """从一行里抽取对话级目标（可选）。"""
    candidates: list[str] = []
    if goal_column:
        candidates.append(goal_column)
    candidates.extend(a for a in _GOAL_COLUMN_ALIASES if a not in candidates)

    lower_map = {str(k).lower().strip(): k for k in row}
    for cand in candidates:
        key = cand if cand in row else lower_map.get(cand.lower())
        if key is None:
            continue
        val = row[key]
        if val not in (None, ""):
            return str(val).strip()
    return None


def collect_sample_values(
    rows: list[dict[str, Any]], headers: list[str], limit: int = 3
) -> dict[str, list[str]]:
    """For the preview UI: first `limit` non-empty values per column, so the
    user can eyeball which column holds the question vs. the answer."""
    samples: dict[str, list[str]] = {h: [] for h in headers if h}
    for row in rows[:limit]:
        for h in headers:
            if not h:
                continue
            val = row.get(h)
            if val is not None and str(val).strip():
                s = str(val).strip()
                samples[h].append(s[:200])
    return samples


def auto_match_columns(
    source_headers: list[str], schema_columns: list[dict]
) -> dict[str, str]:
    """根据 schema 中的 mapped 字段，自动匹配源文件列名。"""
    mapping = {}
    source_lower = {h.lower().strip(): h for h in source_headers}

    mapped_cols = [c for c in schema_columns if c.get("type") == "mapped"]
    for col in mapped_cols:
        col_name = col["name"]
        # 精确匹配
        if col_name.lower() in source_lower:
            mapping[col_name] = source_lower[col_name.lower()]
            continue
        # 别名匹配
        if col_name in COLUMN_ALIASES:
            for alias in COLUMN_ALIASES[col_name]:
                if alias.lower() in source_lower:
                    mapping[col_name] = source_lower[alias.lower()]
                    break

    return mapping


def resolve_extra_fields(
    row_data: dict[str, Any],
    schema_columns: list[dict],
    field_mapping: dict[str, str],
    source_filename: str,
) -> dict[str, Any]:
    """根据 schema 配置，从源数据行解析出 extra_fields。"""
    extra = {}

    for col_def in schema_columns:
        col_name = col_def["name"]
        col_type = col_def.get("type", "mapped")

        if col_type == "mapped":
            src_key = field_mapping.get(col_name)
            if src_key and src_key in row_data:
                val = row_data[src_key]
                if val is not None:
                    extra[col_name] = str(val).strip() if not isinstance(val, (bool, int, float)) else val

        elif col_type == "fixed":
            extra[col_name] = col_def.get("value")

        elif col_type == "auto_detect":
            source_col = col_def.get("source", "")
            src_key = field_mapping.get(source_col)
            text = ""
            if src_key and src_key in row_data:
                text = str(row_data[src_key] or "")
            extra[col_name] = detect_language(text)

        elif col_type == "auto_extract_model":
            source_col = col_def.get("source", "")
            src_key = field_mapping.get(source_col)
            text = ""
            if src_key and src_key in row_data:
                text = str(row_data[src_key] or "")
            model = extract_truck_model(text)
            if model:
                extra[col_name] = model

    return extra


def get_question_from_row(
    row_data: dict[str, Any], schema_columns: list[dict], field_mapping: dict[str, str]
) -> str | None:
    """从源数据行中提取 question 字段（benchmark_cases 的核心字段）。"""
    # 优先从 schema 中找 question 或 input 类型的 mapped 字段
    for col_name in ("question", "input"):
        src_key = field_mapping.get(col_name)
        if src_key and src_key in row_data:
            val = row_data[src_key]
            if val:
                return str(val).strip()

    # fallback: 直接从 row_data 中找常见字段名
    for key in ("question", "input", "问题", "输入"):
        if key in row_data and row_data[key]:
            return str(row_data[key]).strip()

    return None


def get_answer_from_row(
    row_data: dict[str, Any], schema_columns: list[dict], field_mapping: dict[str, str]
) -> str | None:
    """从源数据行中提取 answer 字段。"""
    for col_name in ("expected_answer", "reference_answer", "reference_response", "expected_output"):
        src_key = field_mapping.get(col_name)
        if src_key and src_key in row_data:
            val = row_data[src_key]
            if val and str(val).strip():
                return str(val).strip()

    for key in ("reference_answer", "answer", "参考答案", "expected_answer", "response"):
        if key in row_data and row_data[key]:
            val = str(row_data[key]).strip()
            if val:
                return val

    return None
