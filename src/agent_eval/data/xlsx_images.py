"""从 xlsx 里提取内嵌（浮动）图片，并按行归属到数据行。

真实录入路径里，用户给图片的方式几乎不是「填 URL」，而是**在 Excel 里把图片
贴在问题那一行旁边**。这类图片不是单元格值：xlsx 把它们存成 drawing 锚点，
锚点里记的是「图片左上角落在第几行第几列」，单元格本身仍是空的。故
``ws.iter_rows()`` 那条路无论怎么读都拿不到它们。

为什么不用 openpyxl 的 ``ws._images``：openpyxl 读图依赖 Pillow
（``openpyxl/reader/drawings.py``::``find_images`` 里 ``if not PILImage:
return charts, images``——没装 Pillow 时**静默丢弃全部图片**），而本项目没有
Pillow 依赖，加一个纯为读图的重依赖不值得。xlsx 本身就是个 zip：

    xl/media/image1.png            ← 图片字节
    xl/drawings/drawing1.xml       ← 锚点：<xdr:from><xdr:row>3</xdr:row>
    xl/drawings/_rels/drawing1.xml.rels  ← r:embed → ../media/image1.png
    xl/worksheets/_rels/sheet1.xml.rels  ← 工作表 → drawing1.xml

所以这里用 stdlib ``zipfile`` + ``ElementTree`` 直接解，零新增依赖，也顺带
绕开了 Pillow 那条静默丢弃分支。

产出 ``{excel 行号(1-based): [canonical attachment block, ...]}``，由导入侧
按行号贴到对应样例的 question / 消息上。
"""

from __future__ import annotations

import base64
import io
import posixpath
import re
import zipfile
from xml.etree import ElementTree as ET

# drawing / relationship / 表格部件的命名空间。
_NS = {
    "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
}

# 扩展名 → media_type。canonical source 需要 media_type；xlsx 里图片一律是
# 这几种（Excel 粘贴后统一转码），落单的按 octet-stream 兜底。
_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".emf": "image/emf",
    ".wmf": "image/wmf",
}

# 单张图上限：与前端本地选文件的门禁一致（base64 后约 6.7MB 进 JSONB）。
# 超限的图跳过而非报错——用户一次导入几百行，不该因为一张大图整批失败。
MAX_IMAGE_BYTES = 5 * 1024 * 1024

# 单个文件最多带多少张图，防一张塞满截图的表把库撑爆。
MAX_IMAGES_PER_FILE = 500

# 矢量格式（emf/wmf）多是 Office 自己塞的装饰图元，且下游 agent 也吃不了，跳过。
_SKIP_EXTS = (".emf", ".wmf")


def _media_type(target: str) -> str:
    ext = posixpath.splitext(target)[1].lower()
    return _MEDIA_TYPES.get(ext, "application/octet-stream")


def _rels_for(part_path: str, zf: zipfile.ZipFile) -> dict[str, str]:
    """读某个部件的 .rels，返回 {rId: 解析后的绝对 zip 路径}。"""
    base = posixpath.dirname(part_path)
    rels_path = posixpath.join(base, "_rels", posixpath.basename(part_path) + ".rels")
    if rels_path not in zf.namelist():
        return {}
    out: dict[str, str] = {}
    try:
        root = ET.fromstring(zf.read(rels_path))
    except ET.ParseError:
        return {}
    for rel in root.findall("rel:Relationship", _NS):
        rid = rel.get("Id")
        target = rel.get("Target") or ""
        if not rid or not target:
            continue
        # Target 是相对本部件目录的路径（可能带 ../），normpath 拉平。
        if target.startswith("/"):
            resolved = target.lstrip("/")
        else:
            resolved = posixpath.normpath(posixpath.join(base, target))
        out[rid] = resolved
    return out


def _first_sheet_path(zf: zipfile.ZipFile) -> str | None:
    """取工作簿里第一个工作表的 zip 路径（与 openpyxl 的 wb.active 对齐）。

    直接猜 ``xl/worksheets/sheet1.xml`` 会在「删过工作表」的文件上错位（编号
    不连续），故老实走 workbook.xml + 它的 rels。
    """
    wb_path = "xl/workbook.xml"
    if wb_path not in zf.namelist():
        return None
    try:
        root = ET.fromstring(zf.read(wb_path))
    except ET.ParseError:
        return None
    sheets = root.find("main:sheets", _NS)
    if sheets is None:
        return None
    first = sheets.find("main:sheet", _NS)
    if first is None:
        return None
    rid = first.get(f"{{{_NS['r']}}}id")
    if not rid:
        return None
    return _rels_for(wb_path, zf).get(rid)


def _drawing_paths(zf: zipfile.ZipFile, sheet_path: str) -> list[str]:
    """取某工作表引用的所有 drawing 部件路径。"""
    if sheet_path not in zf.namelist():
        return []
    try:
        root = ET.fromstring(zf.read(sheet_path))
    except ET.ParseError:
        return []
    rels = _rels_for(sheet_path, zf)
    out: list[str] = []
    for tag in ("main:drawing", "main:legacyDrawing"):
        for node in root.findall(tag, _NS):
            rid = node.get(f"{{{_NS['r']}}}id")
            if rid and rid in rels:
                out.append(rels[rid])
    return out


def _anchor_row(anchor: ET.Element) -> int | None:
    """锚点 → 图片左上角所在的 Excel 行号（1-based）。

    xdr:from/xdr:row 是 0-based，+1 换成用户在 Excel 里看到的行号。
    oneCellAnchor / twoCellAnchor 都有 from；absoluteAnchor 只有绝对坐标
    （按像素定位，与行无关），归不到行上，返回 None 由调用方丢弃。
    """
    frm = anchor.find("xdr:from", _NS)
    if frm is None:
        return None
    row_node = frm.find("xdr:row", _NS)
    if row_node is None or not (row_node.text or "").strip():
        return None
    try:
        return int(row_node.text.strip()) + 1
    except ValueError:
        return None


def _blip_embed(anchor: ET.Element) -> str | None:
    """锚点里图片的 rId（a:blip/@r:embed）。"""
    for blip in anchor.iter(f"{{{_NS['a']}}}blip"):
        rid = blip.get(f"{{{_NS['r']}}}embed")
        if rid:
            return rid
    return None


def _anchor_name(anchor: ET.Element) -> str | None:
    """图片的展示名（xdr:nvPicPr/xdr:cNvPr/@name），没有就 None。"""
    for cnv in anchor.iter(f"{{{_NS['xdr']}}}cNvPr"):
        name = (cnv.get("name") or "").strip()
        if name:
            return name
    return None


def extract_row_images(content: bytes) -> dict[int, list[dict]]:
    """提取 xlsx 内嵌图片，按 Excel 行号归组。

    Args:
        content: xlsx 文件字节。

    Returns:
        ``{excel_row_1based: [canonical image block, ...]}``。
        非 xlsx、无图、解析失败都返回空 dict——**读图是增强而非必需**，任何
        异常都不该让整批导入失败。

    图片块形如::

        {"type": "image",
         "source": {"type": "base64", "media_type": "image/png", "data": "..."},
         "name": "图片 1"}
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except (zipfile.BadZipFile, OSError):
        return {}

    out: dict[int, list[dict]] = {}
    total = 0
    try:
        sheet_path = _first_sheet_path(zf)
        if not sheet_path:
            return {}
        names = set(zf.namelist())

        for drawing_path in _drawing_paths(zf, sheet_path):
            if drawing_path not in names:
                continue
            try:
                droot = ET.fromstring(zf.read(drawing_path))
            except (ET.ParseError, KeyError, OSError):
                continue
            drels = _rels_for(drawing_path, zf)

            # 三种锚点都遍历：twoCell（跨单元格拉伸）、oneCell（贴一个格）、
            # absolute（绝对坐标，归不到行，_anchor_row 会返回 None）。
            for tag in ("twoCellAnchor", "oneCellAnchor", "absoluteAnchor"):
                for anchor in droot.findall(f"xdr:{tag}", _NS):
                    row = _anchor_row(anchor)
                    if row is None:
                        continue
                    rid = _blip_embed(anchor)
                    if not rid:
                        continue
                    target = drels.get(rid)
                    if not target or target not in names:
                        continue
                    if posixpath.splitext(target)[1].lower() in _SKIP_EXTS:
                        continue
                    if total >= MAX_IMAGES_PER_FILE:
                        return out
                    try:
                        raw = zf.read(target)
                    except (KeyError, OSError, zipfile.BadZipFile):
                        continue
                    if not raw or len(raw) > MAX_IMAGE_BYTES:
                        continue
                    block: dict = {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": _media_type(target),
                            "data": base64.b64encode(raw).decode("ascii"),
                        },
                    }
                    name = _anchor_name(anchor)
                    if name:
                        block["name"] = name
                    out.setdefault(row, []).append(block)
                    total += 1
    finally:
        zf.close()
    return out


def has_embedded_images(content: bytes, filename: str) -> bool:
    """快速判断：这个上传文件里是否有内嵌图片（不解码图片字节）。

    用于导入前的提示与「是否需要走带图路径」的分支判断。
    """
    if not _is_xlsx(filename):
        return False
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except (zipfile.BadZipFile, OSError):
        return False
    try:
        return any(
            n.startswith("xl/media/")
            and posixpath.splitext(n)[1].lower() not in _SKIP_EXTS
            for n in zf.namelist()
        )
    finally:
        zf.close()


_XLSX_RE = re.compile(r"\.xlsx$", re.IGNORECASE)


def _is_xlsx(filename: str) -> bool:
    """只有 xlsx 是 zip 包。老式 xls 是二进制 BIFF，取不到内嵌图。"""
    return bool(_XLSX_RE.search(filename or ""))


def row_images_for_upload(content: bytes, filename: str) -> dict[int, list[dict]]:
    """导入侧入口：非 xlsx 直接返回空，xlsx 走 extract_row_images。"""
    if not _is_xlsx(filename):
        return {}
    return extract_row_images(content)
