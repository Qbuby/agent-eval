"""Tests for the benchmark file-import parsing/detection layer."""
from __future__ import annotations

import csv
import io
import json

import openpyxl
import pytest

from agent_eval.data.benchmark_import import (
    auto_detect_field_mapping,
    collect_sample_values,
    iter_upload_rows,
    resolve_question_answer,
)


def _csv_bytes(headers: list[str], rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8")


def _xlsx_bytes(headers: list[str], rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# ── iter_upload_rows: three formats yield rows correctly ──────────────────

def test_iter_csv_rows():
    content = _csv_bytes(["question", "answer"], [["Q1", "A1"], ["Q2", "A2"]])
    headers, it = iter_upload_rows(content, "f.csv")
    assert headers == ["question", "answer"]
    rows = list(it)
    assert len(rows) == 2
    assert rows[0]["question"] == "Q1"
    assert rows[1]["answer"] == "A2"


def test_iter_jsonl_rows():
    content = (
        json.dumps({"question": "Q1", "answer": "A1"}, ensure_ascii=False) + "\n"
        + json.dumps({"question": "Q2", "answer": "A2"}, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    headers, it = iter_upload_rows(content, "f.jsonl")
    assert "question" in headers
    rows = list(it)
    assert len(rows) == 2
    assert rows[0]["question"] == "Q1"


def test_iter_json_array_rows():
    content = json.dumps(
        [{"question": "Q1", "answer": "A1"}, {"question": "Q2", "answer": "A2"}],
        ensure_ascii=False,
    ).encode("utf-8")
    headers, it = iter_upload_rows(content, "f.json")
    rows = list(it)
    assert len(rows) == 2


def test_iter_xlsx_rows_streaming():
    content = _xlsx_bytes(["question", "answer"], [["Q1", "A1"], ["Q2", "A2"], [None, None]])
    headers, it = iter_upload_rows(content, "f.xlsx")
    assert headers == ["question", "answer"]
    rows = list(it)
    # The all-empty row is skipped.
    assert len(rows) == 2
    assert rows[0]["question"] == "Q1"


def test_iter_unsupported_type_raises():
    with pytest.raises(ValueError):
        iter_upload_rows(b"x", "f.txt")


# ── auto_detect_field_mapping: column-name variants ──────────────────────

def test_detect_exact_english():
    m = auto_detect_field_mapping(["question", "reference_answer", "tags"])
    assert m["question"] == "question"
    assert m["reference_answer"] == "reference_answer"


def test_detect_chinese_aliases():
    m = auto_detect_field_mapping(["用户问题", "标准答案"])
    assert m["question"] == "用户问题"
    assert m["reference_answer"] == "标准答案"


def test_detect_misc_aliases():
    m = auto_detect_field_mapping(["prompt", "gold_answer"])
    assert m["question"] == "prompt"
    assert m["reference_answer"] == "gold_answer"


def test_detect_question_only():
    m = auto_detect_field_mapping(["问题", "备注"])
    assert m["question"] == "问题"
    assert m["reference_answer"] is None


def test_detect_no_double_mapping():
    # "答案" is an alias for both question(no) and answer; ensure we don't map
    # the same column to both targets when only one answer-ish column exists.
    m = auto_detect_field_mapping(["answer"])
    # "answer" is an answer alias, not a question alias → question None.
    assert m["question"] is None
    assert m["reference_answer"] == "answer"


# ── resolve_question_answer: override precedence ──────────────────────────

def test_resolve_manual_override_wins():
    row = {"col_a": "the question", "col_b": "the answer", "question": "wrong"}
    q, a = resolve_question_answer(
        row, question_column="col_a", answer_column="col_b",
    )
    assert q == "the question"
    assert a == "the answer"


def test_resolve_falls_back_to_aliases():
    row = {"问题": "Q via alias", "参考答案": "A via alias"}
    q, a = resolve_question_answer(row)
    assert q == "Q via alias"
    assert a == "A via alias"


def test_resolve_empty_override_falls_back():
    # Override column present but value empty → fall back to alias detection.
    row = {"override_col": "", "question": "fallback Q"}
    q, _ = resolve_question_answer(row, question_column="override_col")
    assert q == "fallback Q"


def test_resolve_missing_question_returns_none():
    row = {"foo": "bar"}
    q, a = resolve_question_answer(row)
    assert q is None
    assert a is None


# ── collect_sample_values ─────────────────────────────────────────────────

def test_collect_sample_values():
    rows = [
        {"q": "Q1", "a": "A1"},
        {"q": "Q2", "a": ""},
        {"q": "Q3", "a": "A3"},
        {"q": "Q4", "a": "A4"},
    ]
    samples = collect_sample_values(rows, ["q", "a"], limit=3)
    assert samples["q"] == ["Q1", "Q2", "Q3"]
    # Empty value skipped, so "a" has fewer entries within the first 3 rows.
    assert samples["a"] == ["A1", "A3"]


# ── large file: iterator does not materialize everything eagerly ──────────

def test_large_csv_streams():
    n = 10000
    rows = [[f"Q{i}", f"A{i}"] for i in range(n)]
    content = _csv_bytes(["question", "answer"], rows)
    headers, it = iter_upload_rows(content, "big.csv")
    # Consume lazily, count without building a list.
    count = sum(1 for _ in it)
    assert count == n
