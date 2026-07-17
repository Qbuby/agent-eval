"""飞书多维表格（Bitable）读写封装。

职责边界（刻意分成两层，便于测试与 SDK 版本适配）：

1) **字段归一（纯函数，与 SDK 无关）** —— ``normalize_field_value`` /
   ``records_to_rows`` / ``row_to_fields``。Bitable 单元格并非纯文本：多选是
   list[str]、单选是 str、人员/关联/附件是 list[dict]、超链接是 dict{text,link}、
   数字是 int/float、日期是 ms 时间戳。这些必须先降级成解析器期望的
   str/list/JSON 串，否则导入的 case 内容会变成 dict 的字面量。这层是纯函数，
   可离线单测，不依赖 lark_oapi。

2) **SDK 调用（BitableClient，与鉴权 token 解耦）** —— 分页拉全表 /
   批量写。用 **user_access_token**（用户 OAuth，见 oauth.py）而非 app 身份，
   这样能读写用户自己的私人多维表格，无需把 app 加为表协作者。token 由调用方
   传入，client 只管构造请求。同步 SDK 调用一律 ``asyncio.to_thread`` 包，
   避免卡事件循环。

飞书 API 限制：batch_create 单次最多 500 条记录、有 QPS 限流，故批量写分批 +
指数退避。单元格文本过长同样按 ``_CELL_LIMIT`` 截断（对齐 exporters 的
xlsx 32000 限制思路，Bitable 文本上限更保守取 ~9 万字符，这里取稳妥值）。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Bitable batch_create 单批上限（飞书硬限 500）。
_BATCH_SIZE = 500
# 单元格文本上限。飞书文本字段上限较大，这里取稳妥值防超长富文本撑爆请求。
_CELL_LIMIT = 20000
# 批量写的 QPS 退避（秒）：首批不等，其后每批递增，命中 429/限流再指数退避。
_BATCH_PAUSE = 0.2


class BitableError(RuntimeError):
    """Bitable 读写失败，消息可直接回显给用户（不含 token）。

    ``permission`` 为 True 时表示疑似无权限（app/user 未被授予该表访问），
    上层据此给出「请确认已授权该多维表格」而非笼统报错。
    """

    def __init__(self, message: str, *, permission: bool = False) -> None:
        super().__init__(message)
        self.permission = permission


# ────────────────────────────────────────────────────────────────────────
# 字段归一（纯函数，可离线单测）
# ────────────────────────────────────────────────────────────────────────


def normalize_field_value(value: Any) -> Any:
    """把一个 Bitable 单元格值降级成 str / list / 标量，供下游解析器消费。

    覆盖 Bitable 常见字段返回形态：
      * None                      → ""（缺失单元格）
      * str / int / float / bool  → 原样
      * 数字日期（ms 时间戳）      → 交给上层按字段名判断，这里不猜，原样返回数值
      * 多选 list[str]            → 原样 list
      * 单选 str                  → 原样
      * 人员/关联 list[dict]      → 提取每项的 name/text，降级为 list[str]
      * 附件 list[dict]           → 提取 url/name，降级为 list[str]
      * 超链接 dict{text,link}    → 取 text（无 text 取 link）
      * 富文本 list[dict{text}]   → 拼接各段 text 成一个字符串
      * 其它 dict/list            → JSON 串化兜底（不丢信息，可回读）
    """
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        # 超链接 {text, link} / {link}
        if "text" in value and isinstance(value.get("text"), str):
            return value["text"]
        if "link" in value and isinstance(value.get("link"), str):
            return value["link"]
        # 人员/单条关联 {name} / {id, name}
        if "name" in value and isinstance(value.get("name"), str):
            return value["name"]
        # 兜底：JSON 串化，保留信息可回读
        return json.dumps(value, ensure_ascii=False)

    if isinstance(value, list):
        # 空列表 → 空串（下游按缺失处理）
        if not value:
            return ""
        # 富文本段：[{text: "..."}, ...] → 拼成整串
        if all(isinstance(x, dict) and "text" in x for x in value):
            joined = "".join(str(x.get("text") or "") for x in value)
            return joined
        out: list[str] = []
        for item in value:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                # 附件/人员/关联：优先 name，其次 text，其次 url/file_token
                picked = (
                    item.get("name")
                    or item.get("text")
                    or item.get("url")
                    or item.get("file_token")
                )
                out.append(str(picked) if picked is not None else json.dumps(item, ensure_ascii=False))
            else:
                out.append(str(item))
        return out

    # 未知类型兜底
    return str(value)


def records_to_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 Bitable ``list`` 返回的 records 转成解析器期望的 dict 行列表。

    每条 record 形如 ``{"record_id": "...", "fields": {列名: 单元格值}}``。
    产出的每行 = 归一后的 ``fields``，并附带 ``_record_id`` 便于回写对齐。
    列名即 Bitable 字段名，直接充当 ``parse_conversations`` /
    ``resolve_question_answer`` 的 headers。
    """
    rows: list[dict[str, Any]] = []
    for rec in records:
        fields = rec.get("fields") or {}
        row: dict[str, Any] = {
            k: normalize_field_value(v) for k, v in fields.items()
        }
        rid = rec.get("record_id") or rec.get("id")
        if rid:
            row["_record_id"] = rid
        rows.append(row)
    return rows


def _truncate(text: str) -> str:
    if len(text) <= _CELL_LIMIT:
        return text
    return text[: _CELL_LIMIT - 1] + "…"


def row_to_fields(row: dict[str, Any]) -> dict[str, Any]:
    """把导出行（{列名: 值}）转成 Bitable 记录的 ``fields``。

    值归一到 Bitable 文本字段可接受的形态：标量原样、list 用换行拼成串、
    dict JSON 串化，超长截断。默认全部写文本字段（最稳妥；用户表若已建成
    多选/数字等类型，飞书会尝试按目标类型解析，失败则该字段留空由飞书处理）。
    """
    fields: dict[str, Any] = {}
    for key, value in row.items():
        if key.startswith("_"):  # 内部字段（如 _record_id）不写回
            continue
        if value is None:
            fields[key] = ""
        elif isinstance(value, str):
            fields[key] = _truncate(value)
        elif isinstance(value, (int, float, bool)):
            fields[key] = value
        elif isinstance(value, list):
            fields[key] = _truncate(
                "\n".join(
                    x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)
                    for x in value
                )
            )
        else:
            fields[key] = _truncate(json.dumps(value, ensure_ascii=False))
    return fields


# ────────────────────────────────────────────────────────────────────────
# SDK 调用层（与 token 解耦；用 user_access_token）
# ────────────────────────────────────────────────────────────────────────


class BitableClient:
    """封装 lark_oapi bitable.v1 的读写，用 **app 身份**（tenant_access_token）
    访问多维表格。

    app 身份的 tenant_access_token 由 SDK 内部用 app_id/app_secret 自动换取 +
    刷新，调用时无需显式传 token——故这里不再要求 user OAuth，也不需要公网
    OAuth 回调。代价：app 身份只能访问**本应用已被加为协作者**的多维表格，
    用户需先在目标表「...→ 添加文档应用 / 协作者」里把本应用加进去。

    ``user_access_token`` 形参保留但可选（默认 None = app 身份）：传入非空值时
    改用该 user token 走 ``RequestOption``，兼容未来可能的 user OAuth 回退；
    当前所有调用点均按 app 身份（不传 token）使用。SDK 调用同步阻塞，统一
    ``asyncio.to_thread``。
    """

    def __init__(self, user_access_token: str | None = None) -> None:
        self._token = user_access_token
        self._client: Any = None

    def _lark(self) -> Any:
        if self._client is None:
            from agent_eval.feishu.client import get_lark_client

            client = get_lark_client()
            if client is None:
                raise BitableError("飞书未配置，无法访问多维表格")
            self._client = client
        return self._client

    def _req_option(self) -> Any:
        """构造 SDK RequestOption。

        默认（``self._token`` 为空）走 app 身份：不设 user_access_token，SDK
        自动用 app_id/app_secret 换取的 tenant_access_token。仅当显式传入
        user token 时才改用 user 身份。
        """
        import lark_oapi as lark

        builder = lark.RequestOption.builder()
        if self._token:
            builder = builder.user_access_token(self._token)
        return builder.build()

    @staticmethod
    def _check_resp(resp: Any, *, what: str) -> None:
        """统一校验 lark 响应；失败抛 BitableError（区分权限类）。"""
        if resp is not None and getattr(resp, "success", lambda: True)():
            return
        code = getattr(resp, "code", None)
        msg = getattr(resp, "msg", "") or ""
        # 常见权限码：91402/91403（无权限）。app 身份下无权限 = 本应用未被加为
        # 该多维表格的协作者，提示用户去表里添加应用（而非「重新授权」——app 身份
        # 不走 user OAuth）。99991661/99991663 是 token 失效（SDK 自动刷新兜底）。
        permission = code in (91402, 91403) or "permission" in msg.lower() or "access" in msg.lower()
        raise BitableError(
            f"{what}失败（code={code}）：{msg}"
            + (
                "；本应用无权访问该多维表格，请在目标表右上角「···→ 添加文档应用」"
                "里把本飞书应用加为协作者后重试"
                if permission else ""
            ),
            permission=permission,
        )

    async def list_all_records(
        self, app_token: str, table_id: str, *, max_records: int = 10000
    ) -> list[dict[str, Any]]:
        """分页拉取整表记录（page_token 翻页），归一前的原始 records。

        ``max_records`` 是安全上限，防超大表拖垮内存/请求；到达即停并告警。
        """
        from lark_oapi.api.bitable.v1 import ListAppTableRecordRequest

        records: list[dict[str, Any]] = []
        page_token: str | None = None

        while True:
            builder = (
                ListAppTableRecordRequest.builder()
                .app_token(app_token)
                .table_id(table_id)
                .page_size(500)
            )
            if page_token:
                builder = builder.page_token(page_token)
            req = builder.build()

            def _call() -> Any:
                return self._lark().bitable.v1.app_table_record.list(
                    req, self._req_option()
                )

            resp = await asyncio.to_thread(_call)
            self._check_resp(resp, what="读取多维表格记录")

            data = getattr(resp, "data", None)
            items = getattr(data, "items", None) or []
            for it in items:
                # SDK 对象 → dict：优先 .fields / .record_id 属性
                rec = {
                    "record_id": getattr(it, "record_id", None),
                    "fields": getattr(it, "fields", None) or {},
                }
                records.append(rec)

            if len(records) >= max_records:
                logger.warning(
                    "bitable list hit max_records=%d for table %s; truncating",
                    max_records, table_id,
                )
                break

            has_more = bool(getattr(data, "has_more", False))
            page_token = getattr(data, "page_token", None)
            if not has_more or not page_token:
                break

        return records

    async def batch_create_records(
        self, app_token: str, table_id: str, rows: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """把导出行批量写入表。分批（<=500）+ 批间退避，返回 {created,failed,errors}。"""
        from lark_oapi.api.bitable.v1 import (
            AppTableRecord,
            BatchCreateAppTableRecordRequest,
            BatchCreateAppTableRecordRequestBody,
        )

        created = 0
        failed = 0
        errors: list[str] = []

        for start in range(0, len(rows), _BATCH_SIZE):
            chunk = rows[start : start + _BATCH_SIZE]
            api_records = [
                AppTableRecord.builder().fields(row_to_fields(r)).build()
                for r in chunk
            ]
            req = (
                BatchCreateAppTableRecordRequest.builder()
                .app_token(app_token)
                .table_id(table_id)
                .request_body(
                    BatchCreateAppTableRecordRequestBody.builder()
                    .records(api_records)
                    .build()
                )
                .build()
            )

            def _call() -> Any:
                return self._lark().bitable.v1.app_table_record.batch_create(
                    req, self._req_option()
                )

            try:
                resp = await asyncio.to_thread(_call)
                self._check_resp(resp, what="写入多维表格记录")
                created += len(chunk)
            except BitableError as e:
                failed += len(chunk)
                errors.append(str(e))
                # 权限类错误无需继续尝试后续批次
                if e.permission:
                    raise
            except Exception as e:  # noqa: BLE001
                failed += len(chunk)
                errors.append(f"{type(e).__name__}: {e}")

            if start + _BATCH_SIZE < len(rows):
                await asyncio.sleep(_BATCH_PAUSE)

        return {"created": created, "failed": failed, "errors": errors}
