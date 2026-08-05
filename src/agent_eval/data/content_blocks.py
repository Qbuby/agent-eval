"""样例输入的多模态 content blocks 规范化。

样例的 ``input_messages[*].content`` 历史上恒为字符串。为支持带图片（以及
PDF / 视频等附件）的样例，content 现在同时接受 Anthropic canonical blocks
数组形式::

    [{"type": "text", "text": "这张图里的报警码是什么？"},
     {"type": "image", "source": {"type": "url", "url": "https://.../a.png"}}]

选这套 canonical 形状的原因：被测 agent（OmniAgent）的
``ChatAgentRequest.question`` 本身就是 ``str | list[dict]``，其
``content_preprocess`` 三段流水线（URL 下载落沙箱 → 两层 block 组装 →
按 provider 归一化）直接吃这个形状。评测侧只需原样透传，不做任何格式转换。

本模块是全链路唯一的规范化入口，提供：

- ``normalize_content``：把用户/导入侧的宽松写法收敛成 canonical blocks，
  纯文本恒等地退化为字符串（保证无附件样例的行为与改造前逐字节一致）。
- ``content_to_text``：抽出纯文本投影，供落库快照、judge prompt、列表预览
  等只认字符串的既有消费方使用。
- ``content_attachments`` / ``has_attachments``：取出附件块，供 UI 展示与
  校验统计。

宽松写法的容错来自真实录入路径：手填 JSON、Excel 导入的 URL 单元格、以及
OpenAI 风格的 ``{"type": "image_url", "image_url": {"url": ...}}``。
"""

from __future__ import annotations

import re
from typing import Any

# 附件块类型 → 该类型在 canonical 形状下的 source 键。
# 与 OmniAgent content_preprocess._UPLOADABLE_BLOCK_TYPES 对齐：image /
# document / video 三类走「下载落沙箱 + 两层 block」流水线。
ATTACHMENT_TYPES = ("image", "document", "video")

# data URL：data:image/png;base64,xxxx
_DATA_URL_RE = re.compile(
    r"^data:(?P<media_type>[\w.+-]+/[\w.+-]+);base64,(?P<data>.+)$",
    re.DOTALL,
)

# 按扩展名猜附件类型，用于「只给了一个裸 URL」的宽松写法。
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
_DOC_EXTS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".txt")
_VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".webm")

MAX_ATTACHMENTS_PER_MESSAGE = 20


class ContentValidationError(ValueError):
    """content 结构非法（对外可直接作为 400 的 detail 文案）。"""


def guess_attachment_type(url: str) -> str:
    """按 URL 扩展名猜附件块类型，猜不出时按 image 处理。

    猜不出默认 image 是有意的：带图样例是主场景，而 OmniAgent 落沙箱时会用
    magic bytes 重新判定真实类型（``_ensure_image_filename``），猜错不会导致
    链路失败，只影响 UI 图标。
    """
    path = url.split("?", 1)[0].split("#", 1)[0].lower()
    if path.endswith(_VIDEO_EXTS):
        return "video"
    if path.endswith(_DOC_EXTS):
        return "document"
    if path.endswith(_IMAGE_EXTS):
        return "image"
    return "image"


def _normalize_source(block: dict[str, Any], block_type: str) -> dict[str, Any]:
    """把一个附件块的 source 收敛成 canonical 形状。

    接受三种写法：
    - canonical：``{"source": {"type": "url", "url": ...}}`` /
      ``{"source": {"type": "base64", "media_type": ..., "data": ...}}``
    - 扁平：``{"url": ...}``（顶层直接给 URL）
    - OpenAI 风格：``{"image_url": {"url": ...}}``（含 data URL）
    """
    source = block.get("source")
    if isinstance(source, dict):
        stype = source.get("type")
        if stype == "url":
            url = str(source.get("url") or "").strip()
            if not url:
                raise ContentValidationError(f"{block_type} 块的 source.url 不能为空")
            # data URL 写在 type="url" 的 source 里也要拆成 base64 source：
            # 下游把 type="url" 当 HTTP 地址去拉，``data:`` 拉不动。扁平写法
            # （``{"url": "data:..."}``）本就在 _source_from_url 里做了这层转换，
            # 这里补齐，让两种写法归一到同一结果。
            m = _DATA_URL_RE.match(url)
            if m:
                return {
                    "type": "base64",
                    "media_type": m.group("media_type"),
                    "data": m.group("data"),
                }
            return {"type": "url", "url": url}
        if stype == "base64":
            data = str(source.get("data") or "").strip()
            if not data:
                raise ContentValidationError(f"{block_type} 块的 source.data 不能为空")
            return {
                "type": "base64",
                "media_type": str(source.get("media_type") or "").strip()
                or "application/octet-stream",
                "data": data,
            }
        raise ContentValidationError(
            f"{block_type} 块的 source.type 只支持 url / base64，收到 {stype!r}"
        )

    # 扁平 / OpenAI 风格
    raw_url = block.get("url")
    if raw_url is None:
        image_url = block.get("image_url")
        if isinstance(image_url, dict):
            raw_url = image_url.get("url")
        elif isinstance(image_url, str):
            raw_url = image_url
    url = str(raw_url or "").strip()
    if not url:
        raise ContentValidationError(f"{block_type} 块缺少 source / url")
    return _source_from_url(url, block_type)


def _source_from_url(url: str, block_type: str) -> dict[str, Any]:
    """裸 URL 字符串 → canonical source。data URL 转 base64 source。"""
    m = _DATA_URL_RE.match(url)
    if m:
        return {
            "type": "base64",
            "media_type": m.group("media_type"),
            "data": m.group("data"),
        }
    if not url.startswith(("http://", "https://")):
        raise ContentValidationError(
            f"{block_type} 的 URL 必须是 http(s) 或 data:...;base64 形式，收到 {url[:60]!r}"
        )
    return {"type": "url", "url": url}


def _normalize_block(block: Any) -> dict[str, Any] | None:
    """规范化单个 block；返回 None 表示该块是空文本，应被丢弃。"""
    # 裸字符串按文本块处理（手填 JSON 常见写法）
    if isinstance(block, str):
        text = block.strip()
        return {"type": "text", "text": block} if text else None

    if not isinstance(block, dict):
        raise ContentValidationError(
            f"content 数组的元素必须是对象或字符串，收到 {type(block).__name__}"
        )

    btype = str(block.get("type") or "").strip()

    # 没写 type 但给了 URL/图片键：按附件猜类型
    if not btype:
        if "text" in block:
            btype = "text"
        elif block.get("url") or block.get("image_url"):
            probe = _normalize_source(block, "attachment")
            btype = (
                guess_attachment_type(probe["url"])
                if probe["type"] == "url"
                else _type_from_media_type(probe.get("media_type", ""))
            )
        else:
            raise ContentValidationError("content 块缺少 type 字段")

    if btype == "text":
        text = block.get("text")
        if text is None:
            return None
        text = str(text)
        return {"type": "text", "text": text} if text.strip() else None

    # OpenAI 风格别名
    if btype == "image_url":
        btype = "image"
    elif btype in ("input_audio", "audio"):
        raise ContentValidationError("暂不支持音频附件")

    if btype not in ATTACHMENT_TYPES:
        raise ContentValidationError(
            f"不支持的 content 块类型 {btype!r}，"
            f"可用：text / {' / '.join(ATTACHMENT_TYPES)}"
        )

    source = _normalize_source(block, btype)
    out: dict[str, Any] = {"type": btype, "source": source}
    # 保留可选的展示用文件名（UI 展示附件名，不进 agent payload 的语义）
    name = block.get("name") or block.get("filename")
    if name:
        out["name"] = str(name)
    return out


def _type_from_media_type(media_type: str) -> str:
    if media_type.startswith("video/"):
        return "video"
    if media_type.startswith("image/"):
        return "image"
    return "document"


def normalize_content(content: Any) -> str | list[dict[str, Any]]:
    """把宽松的 content 收敛成 canonical 形状。

    - 字符串 / None → 字符串（原样，不做 strip，保持既有落库字节一致）
    - 数组且**不含附件块** → 降级为拼接后的纯文本字符串
    - 数组且含附件块 → canonical blocks 数组

    「无附件则降级为字符串」是关键设计：这样纯文本样例经过本函数后与改造前
    完全一致，存量数据、导出列、judge prompt 都零影响。

    Raises:
        ContentValidationError: 结构非法（缺 type、URL 为空、类型不支持等）。
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise ContentValidationError(
            f"content 必须是字符串或数组，收到 {type(content).__name__}"
        )

    blocks: list[dict[str, Any]] = []
    for block in content:
        normalized = _normalize_block(block)
        if normalized is not None:
            blocks.append(normalized)

    attachments = [b for b in blocks if b["type"] in ATTACHMENT_TYPES]
    if len(attachments) > MAX_ATTACHMENTS_PER_MESSAGE:
        raise ContentValidationError(
            f"单条消息最多 {MAX_ATTACHMENTS_PER_MESSAGE} 个附件，收到 {len(attachments)}"
        )
    if not attachments:
        # 无附件：降级成纯文本，与改造前行为逐字节一致
        return "\n".join(b["text"] for b in blocks if b["type"] == "text")
    return blocks


def content_to_text(content: Any) -> str:
    """抽出 content 的纯文本投影。

    附件块渲染成占位标记（``[图片]`` / ``[文档]`` / ``[视频]``），使只认字符串
    的既有消费方（落库 question 快照、judge prompt、列表预览、导出列）在带图
    样例上也能拿到可读文本，而不是 ``str(list)`` 那种 JSON 噪音。
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    labels = {"image": "[图片]", "document": "[文档]", "video": "[视频]"}
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        btype = str(block.get("type") or "")
        if btype == "text":
            text = block.get("text")
            if text:
                parts.append(str(text))
        elif btype in labels:
            name = block.get("name")
            parts.append(f"{labels[btype]}{name and f'({name})' or ''}")
        elif btype == "image_url":
            parts.append(labels["image"])
    return "\n".join(p for p in parts if p)


def content_attachments(content: Any) -> list[dict[str, Any]]:
    """取出 content 里的附件块（已规范化的 canonical 形状）。

    非数组 content 恒返回空列表。解析失败的块被跳过而不抛错——本函数用于
    展示/统计路径，不做校验（校验走 ``normalize_content``）。
    """
    if not isinstance(content, list):
        return []
    out: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = str(block.get("type") or "")
        if btype == "image_url":
            btype = "image"
        if btype not in ATTACHMENT_TYPES:
            continue
        try:
            source = _normalize_source(block, btype)
        except ContentValidationError:
            continue
        item: dict[str, Any] = {"type": btype, "source": source}
        if block.get("name"):
            item["name"] = str(block["name"])
        out.append(item)
    return out


def has_attachments(content: Any) -> bool:
    """content 是否带附件。"""
    return bool(content_attachments(content))


def messages_have_attachments(messages: Any) -> bool:
    """整段 input_messages 里是否有任一消息带附件。"""
    if not isinstance(messages, list):
        return False
    return any(
        has_attachments(m.get("content")) for m in messages if isinstance(m, dict)
    )


def split_question_content(
    question: Any,
) -> tuple[str, list[dict[str, Any]] | None]:
    """把单轮样例的 question 拆成「纯文本投影, blocks 或 None」。

    单轮两张表（``benchmark_cases`` / ``candidate_cases``）的 ``question`` 是
    ``Text`` 列，没有 blocks 的位置。与其把列类型改成 JSONB（去重、``ilike``
    搜索、导出列、judge prompt 全都直接读这一列），这里选择双写：

    - ``question``：恒为纯文本投影，附件渲染成 ``[图片]`` 占位。既有消费方
      一行都不用改。
    - ``question_content``：仅在带附件时存 canonical blocks，纯文本样例存
      ``NULL``——存量行与新的纯文本行在库里完全同形，无需回填。

    评估组装 cases 时优先取 ``question_content``，回落 ``question``；两者的
    文本部分同源，故回落不会丢内容，只会丢附件。

    Raises:
        ContentValidationError: 结构非法（透传 ``normalize_content`` 的判定）。
    """
    normalized = normalize_content(question)
    if isinstance(normalized, str):
        return normalized, None
    return content_to_text(normalized), normalized


def merge_question_content(
    question: str | None, question_content: Any,
) -> str | list[dict[str, Any]]:
    """``split_question_content`` 的逆操作，供评估组装与前端回填使用。

    带附件时返回 blocks（agent 侧原样吃），否则返回纯文本字符串。
    ``question_content`` 存在但不含附件（异常数据）时也回落到字符串，避免把
    半截数组送进 adapter。
    """
    if isinstance(question_content, list) and has_attachments(question_content):
        return question_content
    return question or ""


# judge prompt 里最多带几张图。上限比录入侧的 MAX_ATTACHMENTS_PER_MESSAGE(20)
# 更紧：judge 一次调用要同时装 system 契约 + 问题 + 回答 + 要点，20 张图很容易
# 顶爆 context 或触发 provider 的单请求图片数限制。超出部分退化成占位符。
JUDGE_MAX_ATTACHMENTS = 8


def _source_to_data_url(source: dict[str, Any]) -> str:
    """canonical source → 单个 URL 字符串（base64 源拼成 data URL）。

    OpenAI ``image_url`` 只认一个 URL 字段，base64 必须编成 data URL；
    Anthropic 则直接吃 canonical source，不走本函数。
    """
    if source.get("type") == "base64":
        media_type = source.get("media_type") or "image/png"
        return f"data:{media_type};base64,{source.get('data') or ''}"
    return str(source.get("url") or "")


def attachments_to_openai_blocks(
    attachments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """canonical 附件块 → OpenAI ``/chat/completions`` 的多模态块。

    只转 ``image``：``/chat/completions`` 的 content 数组只有 ``image_url``
    这一种非文本块，document / video 没有对应形状（要走 Files API 另一套
    协议），故跳过——它们在文本投影里仍是 ``[文档]`` / ``[视频]`` 占位。
    """
    out: list[dict[str, Any]] = []
    for item in attachments[:JUDGE_MAX_ATTACHMENTS]:
        if item.get("type") != "image":
            continue
        url = _source_to_data_url(item.get("source") or {})
        if url:
            out.append({"type": "image_url", "image_url": {"url": url}})
    return out


def attachments_to_anthropic_blocks(
    attachments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """canonical 附件块 → Anthropic ``/v1/messages`` 的多模态块。

    canonical 形状本就照 Anthropic 抄的，所以 image 块只需剥掉展示用的
    ``name`` 字段（API 会拒绝未知键）。document 同样跳过：PDF 虽被
    ``/v1/messages`` 支持，但 media_type 必须是 ``application/pdf`` 且行为
    与图片不同，留待需要时单独做。
    """
    out: list[dict[str, Any]] = []
    for item in attachments[:JUDGE_MAX_ATTACHMENTS]:
        if item.get("type") != "image":
            continue
        source = item.get("source") or {}
        if source.get("type") == "url":
            out.append({"type": "image", "source": {"type": "url", "url": source.get("url") or ""}})
        elif source.get("type") == "base64":
            out.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": source.get("media_type") or "image/png",
                    "data": source.get("data") or "",
                },
            })
    return out


def normalize_messages(messages: Any) -> list[dict[str, Any]]:
    """逐条规范化 input_messages 的 content，其余字段原样保留。

    Raises:
        ContentValidationError: 任一条消息的 content 结构非法（错误文案带上
            消息下标，便于前端定位到具体那一轮）。
    """
    if not isinstance(messages, list):
        raise ContentValidationError("input_messages 必须是数组")
    out: list[dict[str, Any]] = []
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            raise ContentValidationError(
                f"input_messages[{idx}] 必须是对象，收到 {type(msg).__name__}"
            )
        item = dict(msg)
        try:
            item["content"] = normalize_content(msg.get("content"))
        except ContentValidationError as e:
            raise ContentValidationError(f"input_messages[{idx}]: {e}") from e
        out.append(item)
    return out
