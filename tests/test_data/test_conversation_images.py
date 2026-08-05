"""多轮对话导入的内嵌图片落位回归测试。

``xlsx_images`` 负责把粘在 Excel 单元格上的截图按锚点行号取出来（那一层的边界由
``test_xlsx_images`` 覆盖），这里覆盖的是**下一段**：取出来的图该挂到对话的哪一轮。

三种布局的落位规则不同，规则错了图就会串轮或整段丢失：
  * 布局 C（拍平多行，一行一个 turn）——行与轮一一对应，图挂**该行自己的 user
    轮**。这是最容易错的一档：整段对话跨多行，若按「首轮」处理，第 3 轮的图会
    跑到第 1 轮上。
  * 布局 A/B（行内 chat / QA-turn 数组，一行一整段对话）——锚点只有行号，分不出
    轮次，统一挂**第一条 user 轮**（带图多轮的典型形态是首轮发图、后续追问）。
  * 非 xlsx（csv/json）——没有行号也没有图，必须逐字节退化为纯文本：``content``
    仍是 ``str`` 而不是 blocks 数组，否则纯文本对话的存量数据形状会被改掉。

另外钉住两条不变量：assistant 轮永不带图（图是用户发的），以及行迭代器注入的
保留键 ``__excel_row__`` 不能泄漏进任何用户可见字段。
"""
from __future__ import annotations

import base64
import csv
import io

from agent_eval.data.benchmark_import import (
    ROW_INDEX_KEY,
    iter_upload_rows,
    parse_conversations,
)
from agent_eval.data.xlsx_images import row_images_for_upload

from tests.test_data.test_xlsx_images import _PNG, _simple_book

# ── 公共构件 ──


def _parse_xlsx(rows: list[list], anchors: list[tuple[str, int | None, str, str | None]],
                media: dict[str, bytes], **kwargs):
    """构造带图 xlsx → 走完整导入链路 → (conversations, skipped)。

    刻意从字节开始：``iter_upload_rows`` 注入行号、``row_images_for_upload`` 按行
    取图、``parse_conversations`` 按布局落位，三者的接缝正是回归高发处。
    """
    content = _simple_book(anchors, media, rows=rows)
    _, row_iter = iter_upload_rows(content, "conv.xlsx")
    return parse_conversations(
        row_iter, row_images=row_images_for_upload(content, "conv.xlsx"), **kwargs,
    )


def _img_blocks(content) -> list[dict]:
    """content 里的 image 块。纯文本 content 是 str，返回空列表。"""
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == "image"]


def _text_of(content) -> str:
    """content 的文本部分（str 原样，blocks 数组拼 text 块）。"""
    if isinstance(content, str):
        return content
    return "".join(
        b.get("text", "") for b in content
        if isinstance(b, dict) and b.get("type") == "text"
    )


def _users(conv):
    return [m for m in conv.input_messages if m["role"] == "user"]


# ── 布局 C：拍平多行，图各挂自己那一轮 ──


def test_flattened_image_lands_on_its_own_turn():
    """三轮对话，第 1、3 轮带图 → 图各回各轮，不聚到首轮。"""
    rows = [
        ["conversation_id", "turn_no", "question", "answer"],
        ["c1", 1, "这张图里的车是什么型号", "看着像 CPD20"],
        ["c1", 2, "它的载重是多少", "2 吨"],
        ["c1", 3, "这张铭牌上的编号呢", "编号 A-118"],
    ]
    # Excel 第 2 行 = 第 1 轮，第 4 行 = 第 3 轮（xdr:row 是 0-based）
    convs, skipped = _parse_xlsx(
        rows,
        [("twoCellAnchor", 1, "rId1", "车辆照片"),
         ("twoCellAnchor", 3, "rId2", "铭牌照片")],
        {"image1.png": _PNG, "image2.png": _PNG},
    )
    assert skipped == 0
    assert len(convs) == 1
    users = _users(convs[0])
    assert len(users) == 3

    assert len(_img_blocks(users[0]["content"])) == 1
    assert "车是什么型号" in _text_of(users[0]["content"])
    # 中间那轮没图 → content 必须仍是 str，纯文本轮的形状不被带图改造波及
    assert isinstance(users[1]["content"], str)
    assert len(_img_blocks(users[2]["content"])) == 1
    assert "铭牌上的编号" in _text_of(users[2]["content"])


def test_flattened_assistant_turns_never_carry_images():
    """图是用户发的，assistant 轮恒不带图（否则回放会把图当模型输出）。"""
    rows = [
        ["conversation_id", "turn_no", "question", "answer"],
        ["c1", 1, "这张图里是什么", "一辆叉车"],
    ]
    convs, _ = _parse_xlsx(
        rows, [("twoCellAnchor", 1, "rId1", "图")], {"image1.png": _PNG},
    )
    assistants = [m for m in convs[0].input_messages if m["role"] == "assistant"]
    assert assistants
    assert all(not _img_blocks(m["content"]) for m in assistants)


def test_flattened_images_do_not_cross_conversations():
    """两段对话各带一张图 → 不跨段串位（分组后仍按行号定位）。"""
    rows = [
        ["conversation_id", "turn_no", "question", "answer"],
        ["cA", 1, "A 段第一问带图", "答"],
        ["cA", 2, "A 段第二问", "答"],
        ["cB", 1, "B 段第一问", "答"],
        ["cB", 2, "B 段第二问带图", "答"],
    ]
    # 第 2 行 = A 段第 1 轮；第 5 行 = B 段第 2 轮
    convs, _ = _parse_xlsx(
        rows,
        [("twoCellAnchor", 1, "rId1", "A图"), ("twoCellAnchor", 4, "rId2", "B图")],
        {"image1.png": _PNG, "image2.png": _PNG},
    )
    assert len(convs) == 2
    a, b = convs
    assert len(_img_blocks(_users(a)[0]["content"])) == 1
    assert not _img_blocks(_users(a)[1]["content"])
    assert not _img_blocks(_users(b)[0]["content"])
    assert len(_img_blocks(_users(b)[1]["content"])) == 1


def test_flattened_image_survives_turn_reordering():
    """turn_no 乱序时按轮号重排，图仍跟着它原本那一行走。"""
    rows = [
        ["conversation_id", "turn_no", "question", "answer"],
        ["c1", 2, "第二轮问句", "答"],
        ["c1", 1, "第一轮问句带图", "答"],
    ]
    # 图在 Excel 第 3 行 —— 文件里的第二条数据行，但它是 turn_no=1
    convs, _ = _parse_xlsx(
        rows, [("twoCellAnchor", 2, "rId1", "图")], {"image1.png": _PNG},
    )
    users = _users(convs[0])
    assert "第一轮问句" in _text_of(users[0]["content"])
    assert len(_img_blocks(users[0]["content"])) == 1
    assert not _img_blocks(users[1]["content"])


def test_flattened_image_bytes_roundtrip():
    """图片字节原样透传（base64 能还原出源 PNG）。"""
    rows = [
        ["conversation_id", "turn_no", "question"],
        ["c1", 1, "这是什么"],
    ]
    convs, _ = _parse_xlsx(
        rows, [("twoCellAnchor", 1, "rId1", "图")], {"image1.png": _PNG},
    )
    block = _img_blocks(_users(convs[0])[0]["content"])[0]
    assert block["source"]["media_type"] == "image/png"
    assert base64.b64decode(block["source"]["data"]) == _PNG


def test_flattened_multiple_images_same_turn():
    """同一行贴多张图 → 全部挂到该轮，顺序保留。"""
    rows = [
        ["conversation_id", "turn_no", "question"],
        ["c1", 1, "这两张图有什么区别"],
    ]
    convs, _ = _parse_xlsx(
        rows,
        [("twoCellAnchor", 1, "rId1", "图甲"), ("twoCellAnchor", 1, "rId2", "图乙")],
        {"image1.png": _PNG, "image2.png": _PNG},
    )
    blocks = _img_blocks(_users(convs[0])[0]["content"])
    assert len(blocks) == 2
    assert [b.get("name") for b in blocks] == ["图甲", "图乙"]


# ── 布局 A：行内 chat 数组，图统一挂首条 user 轮 ──


def test_inline_chat_image_lands_on_first_user_turn():
    """一行一整段对话时锚点分不出轮次 → 统一挂首条 user 轮。"""
    messages = (
        '[{"role":"user","content":"这张图里是什么"},'
        '{"role":"assistant","content":"一辆叉车"},'
        '{"role":"user","content":"它能载多重"}]'
    )
    rows = [["messages"], [messages]]
    convs, _ = _parse_xlsx(
        rows, [("twoCellAnchor", 1, "rId1", "图")], {"image1.png": _PNG},
    )
    assert len(convs) == 1
    users = _users(convs[0])
    assert len(_img_blocks(users[0]["content"])) == 1
    assert "这张图里是什么" in _text_of(users[0]["content"])
    assert not _img_blocks(users[1]["content"])
    assistants = [m for m in convs[0].input_messages if m["role"] == "assistant"]
    assert all(not _img_blocks(m["content"]) for m in assistants)


# ── 无图 / 非 xlsx：逐字节退化为纯文本 ──


def test_csv_source_stays_plain_text():
    """csv 没有行号也没有内嵌图 → content 全是 str，不被改造成 blocks。"""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["conversation_id", "turn_no", "question", "answer"])
    w.writerow(["c1", 1, "第一问", "第一答"])
    w.writerow(["c1", 2, "第二问", "第二答"])
    content = buf.getvalue().encode("utf-8")

    _, row_iter = iter_upload_rows(content, "conv.csv")
    convs, skipped = parse_conversations(
        row_iter, row_images=row_images_for_upload(content, "conv.csv"),
    )
    assert skipped == 0
    assert len(convs) == 1
    assert all(isinstance(m["content"], str) for m in convs[0].input_messages)


def test_xlsx_without_images_stays_plain_text():
    """xlsx 但没贴图 → 同样退化为纯文本（空 dict 与 None 走同一分支）。"""
    rows = [
        ["conversation_id", "turn_no", "question", "answer"],
        ["c1", 1, "第一问", "第一答"],
    ]
    convs, _ = _parse_xlsx(rows, [], {})
    assert all(isinstance(m["content"], str) for m in convs[0].input_messages)


def test_row_images_omitted_entirely_is_plain_text():
    """调用方完全不传 row_images（飞书等非文件来源）→ 纯文本路径。"""
    rows = [
        {"conversation_id": "c1", "turn_no": 1, "question": "第一问"},
        {"conversation_id": "c1", "turn_no": 2, "question": "第二问"},
    ]
    convs, _ = parse_conversations(rows)
    assert len(convs) == 1
    assert all(isinstance(m["content"], str) for m in convs[0].input_messages)


# ── 保留键不泄漏 ──


def test_reserved_row_key_never_leaks_into_output():
    """``__excel_row__`` 是行迭代器的内部锚点，不能出现在任何用户可见字段里。"""
    rows = [
        ["conversation_id", "turn_no", "question", "answer", "goal"],
        ["c1", 1, "第一问", "第一答", "确认车型"],
    ]
    convs, _ = _parse_xlsx(
        rows, [("twoCellAnchor", 1, "rId1", "图")], {"image1.png": _PNG},
    )
    for conv in convs:
        assert ROW_INDEX_KEY not in (conv.conversation_goal or "")
        assert ROW_INDEX_KEY not in (conv.name or "")
        for m in conv.input_messages:
            assert ROW_INDEX_KEY not in _text_of(m["content"])
