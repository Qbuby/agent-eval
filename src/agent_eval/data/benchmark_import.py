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
from dataclasses import dataclass, field
from typing import Any

import openpyxl

VEHICLE_MODEL_PATTERN = re.compile(
    r"(RPL[A-Z0-9]+|CPD[A-Z0-9]+|CQD[A-Z0-9]+|EPT[A-Z0-9\-]+|EXP[A-Z0-9]+|"
    r"EPL[A-Z0-9]+|WPL[A-Z0-9]+|DS\d[A-Z0-9]*|KPL[A-Z0-9]+|F\d{4}[A-Z0-9]*)",
    re.IGNORECASE,
)

COLUMN_ALIASES: dict[str, list[str]] = {
    "question": ["question", "问题", "q", "query", "input", "输入"],
    "input": ["input", "输入", "问题", "question", "请求体"],
    "expected_output": ["expected_output", "expected", "预期", "预期结果", "答案"],
    "expected_answer_url": ["expected_answer_url", "url", "expected_keywords", "链接"],
    "expected_answer": ["expected_answer", "answer", "答案", "参考答案", "reference_answer"],
    "reference_response": ["reference_response", "response", "回答", "回复"],
    "reference_answer": ["reference_answer", "answer", "答案", "参考答案"],
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
