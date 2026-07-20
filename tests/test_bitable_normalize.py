"""Bitable 字段归一纯函数的离线单测（stdlib assert，无需 pytest）。

直接 `python test_bitable_normalize.py` 运行；全过打印 OK，任一失败抛 AssertionError。
覆盖：单元格各类型→str/list 归一、富文本拼接、超链接/人员/附件降级、
超长截断、records↔rows round-trip。
"""
from __future__ import annotations

import sys

from agent_eval.feishu.bitable import (
    _CELL_LIMIT,
    normalize_field_value,
    records_to_rows,
    row_to_fields,
)


def test_scalars_passthrough() -> None:
    assert normalize_field_value("hi") == "hi"
    assert normalize_field_value(42) == 42
    assert normalize_field_value(3.14) == 3.14
    assert normalize_field_value(True) is True


def test_none_and_empty_list() -> None:
    assert normalize_field_value(None) == ""
    assert normalize_field_value([]) == ""


def test_multiselect_list_of_str() -> None:
    assert normalize_field_value(["a", "b"]) == ["a", "b"]


def test_hyperlink_dict() -> None:
    assert normalize_field_value({"text": "官网", "link": "https://x.com"}) == "官网"
    assert normalize_field_value({"link": "https://x.com"}) == "https://x.com"


def test_person_dict() -> None:
    assert normalize_field_value({"id": "ou_1", "name": "张三"}) == "张三"


def test_richtext_segments_joined() -> None:
    val = [{"text": "第一段"}, {"text": "第二段"}]
    assert normalize_field_value(val) == "第一段第二段"


def test_person_list_to_names() -> None:
    val = [{"id": "ou_1", "name": "张三"}, {"id": "ou_2", "name": "李四"}]
    assert normalize_field_value(val) == ["张三", "李四"]


def test_attachment_list_to_names() -> None:
    val = [{"name": "a.png", "file_token": "tok1"}, {"file_token": "tok2"}]
    assert normalize_field_value(val) == ["a.png", "tok2"]


def test_unknown_dict_json_fallback() -> None:
    out = normalize_field_value({"weird": {"nested": 1}})
    assert '"weird"' in out and '"nested"' in out


def test_records_to_rows_with_record_id() -> None:
    recs = [
        {"record_id": "rec1", "fields": {"问题": "Q1", "答案": "A1"}},
        {"record_id": "rec2", "fields": {"问题": "Q2", "标签": ["x", "y"]}},
    ]
    rows = records_to_rows(recs)
    assert rows[0] == {"问题": "Q1", "答案": "A1", "_record_id": "rec1"}
    assert rows[1] == {"问题": "Q2", "标签": ["x", "y"], "_record_id": "rec2"}


def test_row_to_fields_drops_internal_and_serializes() -> None:
    row = {"问题": "Q", "分数": 0.8, "标签": ["a", "b"], "_record_id": "rec1"}
    fields = row_to_fields(row)
    assert "_record_id" not in fields  # 内部字段不写回
    assert fields["问题"] == "Q"
    assert fields["分数"] == 0.8
    assert fields["标签"] == "a\nb"  # list 换行拼接


def test_row_to_fields_truncates_long_text() -> None:
    long = "x" * (_CELL_LIMIT + 500)
    fields = row_to_fields({"长文本": long})
    assert len(fields["长文本"]) == _CELL_LIMIT
    assert fields["长文本"].endswith("…")


def test_roundtrip_scalar_and_list() -> None:
    # records → rows → fields，标量与多选应保内容（list 会被拼成串，符合导出预期）
    recs = [{"record_id": "r1", "fields": {"q": "hello", "tags": ["p", "q"]}}]
    rows = records_to_rows(recs)
    fields = row_to_fields(rows[0])
    assert fields["q"] == "hello"
    assert fields["tags"] == "p\nq"
    assert "_record_id" not in fields


def _run_all() -> int:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
