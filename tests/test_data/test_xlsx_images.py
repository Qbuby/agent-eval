"""xlsx 内嵌图片提取的回归测试（#153）。

真实录入路径里，用户是把截图**直接粘到 Excel 单元格上**的——这类图不在任何
单元格的值里，而是作为独立的 drawing 部件挂在工作表上，图片字节躺在
``xl/media/``。``iter_upload_rows`` 走 openpyxl 读值，天生看不到它们，所以
``xlsx_images`` 单独开一条 zip 解析路径，按锚点行号把图贴回对应样例。

这里刻意**不依赖 Pillow**：被测模块只用 stdlib ``zipfile`` + ``ElementTree``
读字节，测试也照此构造——openpyxl 出表体，再用 zipfile 把 drawing 三件套
（sheet 的 ``<drawing r:id>`` 引用 / drawing xml / rels / media 字节）注进包里。
这条路走的是和真实 Excel 文件完全相同的解析分支，比 mock 更能兜住回归。

覆盖的边界都是线上真会遇到的：
  * 行号映射——``xdr:row`` 是 0-based，用户看到的行号是 1-based，差一即错位；
  * ``absoluteAnchor``——按像素定位，归不到行上，必须丢弃而不是算作第 1 行；
  * 超限图 / emf-wmf 装饰图元——跳过单张，不能让整批导入失败；
  * 非 xlsx 与坏包——恒返回空 dict，读图是增强而非必需。
"""
from __future__ import annotations

import base64
import io
import posixpath
import zipfile

import openpyxl

from agent_eval.data.xlsx_images import (
    MAX_IMAGE_BYTES,
    extract_row_images,
    has_embedded_images,
    row_images_for_upload,
)

# ── 公共构件 ──

# 1x1 PNG（真实字节，89 50 4E 47 magic）。内容不重要——被测模块不解码像素，
# 只做 base64 透传，所以无需 Pillow 生成。
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)
_GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")


def _sheet_body(rows: list[list]) -> bytes:
    """用 openpyxl 出一个正常的 xlsx（只有值，没有图）。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _drawing_xml(anchors: list[tuple[str, int | None, str, str | None]]) -> bytes:
    """拼 drawing xml。

    anchors 每项 ``(anchor_tag, row_0based, rid, name)``。row 传 None 时不写
    ``xdr:from``（absoluteAnchor 的形状）。
    """
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"'
        ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">',
    ]
    for tag, row, rid, name in anchors:
        parts.append(f"<xdr:{tag}>")
        if row is not None:
            parts.append(f"<xdr:from><xdr:col>0</xdr:col><xdr:row>{row}</xdr:row></xdr:from>")
        parts.append(
            "<xdr:pic><xdr:nvPicPr>"
            f'<xdr:cNvPr id="1" name="{name or ""}"/><xdr:cNvPicPr/>'
            "</xdr:nvPicPr>"
            f'<xdr:blipFill><a:blip r:embed="{rid}"/></xdr:blipFill>'
            "</xdr:pic>"
        )
        parts.append(f"</xdr:{tag}>")
    parts.append("</xdr:wsDr>")
    return "".join(parts).encode("utf-8")


def _rels_xml(pairs: list[tuple[str, str]]) -> bytes:
    """拼 .rels（rId → Target，Target 相对本部件目录）。"""
    items = "".join(
        f'<Relationship Id="{rid}" Target="{target}"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"/>'
        for rid, target in pairs
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{items}</Relationships>"
    ).encode("utf-8")


def _with_drawing(
    body: bytes,
    anchors: list[tuple[str, int | None, str, str | None]],
    media: dict[str, bytes],
) -> bytes:
    """把 drawing 部件注进一个已有的 xlsx 包。

    做四件事，与 Excel 存盘时的产物一致：
      1. ``xl/media/<name>`` 写图片字节；
      2. ``xl/drawings/drawing1.xml`` 写锚点；
      3. ``xl/drawings/_rels/drawing1.xml.rels`` 把 rId 映到 media；
      4. 在工作表 xml 尾部插 ``<drawing r:id="rIdDr"/>`` 并补进 sheet 的 rels。

    第 4 步必须改写既有部件，而 zipfile 不支持原地替换，故整包重打。
    """
    src = zipfile.ZipFile(io.BytesIO(body))
    try:
        names = src.namelist()
        # openpyxl 出的包里工作表恒为 sheet1.xml，但仍按 workbook rels 取，
        # 与被测模块的 _first_sheet_path 保持同一套认定。
        sheet_path = "xl/worksheets/sheet1.xml"
        assert sheet_path in names, names

        sheet_xml = src.read(sheet_path).decode("utf-8")
        assert "</worksheet>" in sheet_xml
        sheet_xml = sheet_xml.replace(
            "</worksheet>", '<drawing r:id="rIdDr"/></worksheet>'
        )
        # openpyxl 的 sheet 根标签没声明 r: 前缀，得补上，否则 ET 解析报未绑定前缀。
        if 'xmlns:r=' not in sheet_xml.split(">", 2)[1]:
            sheet_xml = sheet_xml.replace(
                "<worksheet ",
                '<worksheet xmlns:r="http://schemas.openxmlformats.org/'
                'officeDocument/2006/relationships" ',
                1,
            )

        sheet_rels_path = "xl/worksheets/_rels/sheet1.xml.rels"
        sheet_rels = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rIdDr" Target="../drawings/drawing1.xml"'
            ' Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/drawing"/>'
            "</Relationships>"
        ).encode("utf-8")

        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                if item.filename in (sheet_path, sheet_rels_path):
                    continue
                dst.writestr(item, src.read(item.filename))
            dst.writestr(sheet_path, sheet_xml.encode("utf-8"))
            dst.writestr(sheet_rels_path, sheet_rels)
            dst.writestr("xl/drawings/drawing1.xml", _drawing_xml(anchors))
            # rId 按 media 的插入顺序编号：rId1 ↔ 第一张图，rId2 ↔ 第二张……
            # 各用例的 anchors 就是按这个约定写 rId 的；写 rIdMissing 即制造悬挂引用。
            dst.writestr(
                "xl/drawings/_rels/drawing1.xml.rels",
                _rels_xml(
                    [
                        (f"rId{i + 1}", f"../media/{name}")
                        for i, name in enumerate(media)
                    ]
                ),
            )
            for name, raw in media.items():
                dst.writestr(f"xl/media/{name}", raw)
        return out.getvalue()
    finally:
        src.close()


def _simple_book(anchors, media, rows=None) -> bytes:
    """表体 + drawing 的常用组合。"""
    body = _sheet_body(rows or [["问题", "答案"], ["这张图里是什么", "一辆卡车"]])
    return _with_drawing(body, anchors, media)


# ── 行号映射 ──

def test_anchor_row_is_one_based():
    """xdr:row=1（0-based）→ Excel 第 2 行。差一错位是这条链路最致命的 bug。"""
    content = _simple_book(
        [("twoCellAnchor", 1, "rId1", "图片 1")], {"image1.png": _PNG}
    )
    got = extract_row_images(content)
    assert list(got) == [2]
    assert len(got[2]) == 1


def test_block_is_canonical_and_roundtrips_bytes():
    """块形状为 canonical image，且 base64 能还原出原字节。"""
    content = _simple_book(
        [("oneCellAnchor", 2, "rId1", "仪表盘")], {"image1.png": _PNG}
    )
    block = extract_row_images(content)[3][0]
    assert block["type"] == "image"
    assert block["name"] == "仪表盘"
    assert block["source"]["type"] == "base64"
    assert block["source"]["media_type"] == "image/png"
    assert base64.b64decode(block["source"]["data"]) == _PNG


def test_media_type_follows_extension():
    """media_type 按 media 的扩展名给，不是一律 png。"""
    content = _simple_book(
        [("oneCellAnchor", 0, "rId1", None)], {"image1.gif": _GIF}
    )
    block = extract_row_images(content)[1][0]
    assert block["source"]["media_type"] == "image/gif"
    assert base64.b64decode(block["source"]["data"]) == _GIF


def test_unnamed_anchor_omits_name_key():
    """没有 name 的锚点不写 name 键（而不是写空串）。"""
    content = _simple_book(
        [("twoCellAnchor", 0, "rId1", None)], {"image1.png": _PNG}
    )
    assert "name" not in extract_row_images(content)[1][0]


def test_multiple_images_same_row_group_together():
    """同一行贴多张图 → 按锚点顺序归到同一个 list。"""
    content = _simple_book(
        [
            ("twoCellAnchor", 1, "rId1", "左"),
            ("oneCellAnchor", 1, "rId2", "右"),
        ],
        {"image1.png": _PNG, "image2.gif": _GIF},
    )
    got = extract_row_images(content)
    assert list(got) == [2]
    assert [b["name"] for b in got[2]] == ["左", "右"]


def test_images_across_rows_keyed_separately():
    content = _simple_book(
        [
            ("twoCellAnchor", 1, "rId1", "a"),
            ("twoCellAnchor", 5, "rId2", "b"),
        ],
        {"image1.png": _PNG, "image2.png": _PNG},
    )
    got = extract_row_images(content)
    assert sorted(got) == [2, 6]


# ── 丢弃与跳过 ──

def test_absolute_anchor_dropped():
    """absoluteAnchor 没有 xdr:from，归不到行——必须丢弃，不能算作第 1 行。"""
    content = _simple_book(
        [("absoluteAnchor", None, "rId1", "浮动图")], {"image1.png": _PNG}
    )
    assert extract_row_images(content) == {}


def test_vector_media_skipped():
    """emf/wmf 多是 Office 塞的装饰图元，下游 agent 也吃不了。"""
    content = _simple_book(
        [("twoCellAnchor", 1, "rId1", "装饰")], {"image1.emf": b"\x01\x00\x00\x00emf"}
    )
    assert extract_row_images(content) == {}


def test_oversize_image_skipped_others_kept():
    """超限的图单张跳过，同文件其它图照旧——一张大图不该毁掉整批导入。"""
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (MAX_IMAGE_BYTES + 1)
    content = _simple_book(
        [
            ("twoCellAnchor", 1, "rId1", "太大"),
            ("twoCellAnchor", 3, "rId2", "正常"),
        ],
        {"image1.png": big, "image2.png": _PNG},
    )
    got = extract_row_images(content)
    assert list(got) == [4]
    assert got[4][0]["name"] == "正常"


def test_dangling_rid_skipped():
    """锚点指向的 rId 在 rels 里没有对应 media → 跳过而非抛错。"""
    body = _sheet_body([["问题"], ["带图"]])
    content = _with_drawing(
        body, [("twoCellAnchor", 1, "rIdMissing", "幽灵")], {"image1.png": _PNG}
    )
    assert extract_row_images(content) == {}


# ── 无图 / 非 xlsx / 坏包 ──

def test_plain_xlsx_yields_nothing():
    assert extract_row_images(_sheet_body([["问题", "答案"], ["纯文本", "无图"]])) == {}


def test_bad_zip_yields_empty_dict():
    """坏包恒返回空 dict——读图是增强而非必需，不该让导入失败。"""
    assert extract_row_images(b"not a zip at all") == {}
    assert extract_row_images(b"") == {}


def test_has_embedded_images_detects_media():
    with_img = _simple_book([("twoCellAnchor", 1, "rId1", None)], {"image1.png": _PNG})
    assert has_embedded_images(with_img, "cases.xlsx") is True
    assert has_embedded_images(_sheet_body([["a"], ["b"]]), "cases.xlsx") is False


def test_has_embedded_images_ignores_vector_only():
    """只有 emf/wmf 的包不算「带图」，否则前端会给出误导性提示。"""
    only_vector = _simple_book(
        [("twoCellAnchor", 1, "rId1", None)], {"image1.wmf": b"\xd7\xcd\xc6\x9awmf"}
    )
    assert has_embedded_images(only_vector, "cases.xlsx") is False


def test_non_xlsx_short_circuits():
    """csv/xls 不是 zip 包，取不到内嵌图；扩展名判断大小写不敏感。"""
    with_img = _simple_book([("twoCellAnchor", 1, "rId1", None)], {"image1.png": _PNG})
    assert has_embedded_images(with_img, "cases.csv") is False
    assert has_embedded_images(with_img, "cases.xls") is False
    assert row_images_for_upload(with_img, "cases.csv") == {}
    assert row_images_for_upload(with_img, "cases.XLSX") != {}
    assert has_embedded_images(with_img, "") is False


def test_row_images_for_upload_matches_extract():
    content = _simple_book([("twoCellAnchor", 1, "rId1", "图")], {"image1.png": _PNG})
    assert row_images_for_upload(content, "cases.xlsx") == extract_row_images(content)


# ── 包结构变体 ──

def test_sheet_resolved_via_workbook_rels():
    """工作表路径走 workbook.xml + rels，而不是硬猜 sheet1.xml。

    删过工作表的文件里编号不连续，硬猜会错位。这里把 sheet1.xml 整体搬到
    sheet7.xml 并改掉 rels 指向，验证仍能取到图。
    """
    body = _simple_book([("twoCellAnchor", 1, "rId1", "图")], {"image1.png": _PNG})
    src = zipfile.ZipFile(io.BytesIO(body))
    try:
        wb_rels = src.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        assert "worksheets/sheet1.xml" in wb_rels
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                name = item.filename
                raw = src.read(name)
                if name == "xl/_rels/workbook.xml.rels":
                    raw = wb_rels.replace(
                        "worksheets/sheet1.xml", "worksheets/sheet7.xml"
                    ).encode("utf-8")
                    name = "xl/_rels/workbook.xml.rels"
                elif name == "xl/worksheets/sheet1.xml":
                    name = "xl/worksheets/sheet7.xml"
                elif name == "xl/worksheets/_rels/sheet1.xml.rels":
                    name = "xl/worksheets/_rels/sheet7.xml.rels"
                elif name == "[Content_Types].xml":
                    raw = raw.replace(b"sheet1.xml", b"sheet7.xml")
                dst.writestr(name, raw)
        moved = out.getvalue()
    finally:
        src.close()

    assert posixpath.basename("xl/worksheets/sheet7.xml") == "sheet7.xml"
    assert list(extract_row_images(moved)) == [2]


def test_image_count_capped(monkeypatch):
    """超过单文件上限后停止收集，防截图表把库撑爆。"""
    monkeypatch.setattr("agent_eval.data.xlsx_images.MAX_IMAGES_PER_FILE", 2)
    anchors = [("twoCellAnchor", i, f"rId{i + 1}", f"图{i}") for i in range(5)]
    media = {f"image{i + 1}.png": _PNG for i in range(5)}
    got = _simple_book(anchors, media)
    total = sum(len(v) for v in extract_row_images(got).values())
    assert total == 2
