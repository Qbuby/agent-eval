"""把评估结果 / 对比矩阵导出到飞书多维表格（Bitable）。

与 ``exporters.py`` 平级、共用同一套数据源与列谱（``ExportColumn`` +
``_collect_run_results`` 产出的 rows），只把出口从「序列化成 attachment
Response」换成「batch_create 成 Bitable 记录」。这样单次结果 /
对比矩阵 / summary 三条导出路径都能零改动切换目的地。

流程：rows + columns → ``build_bitable_records``（用 ``ExportColumn.value``
取值，键用 header 便于人读）→ ``BitableClient.batch_create_records``
（内部 ``row_to_fields`` 归一 + 分批 + 退避）。token 用 user OAuth
（``user_access_token``），故写入的是用户自己的私人多维表格。
"""
from __future__ import annotations

import logging
from typing import Any

from agent_eval.api.exporters import ExportColumn
from agent_eval.feishu.bitable import BitableClient

logger = logging.getLogger(__name__)


def build_bitable_records(
    rows: list[dict[str, Any]], columns: list[ExportColumn]
) -> list[dict[str, Any]]:
    """把导出 rows 按列谱投影成 Bitable 写入行（``{列头: 值}``）。

    键用 ``ExportColumn.header``（中文列名，与 CSV/xlsx 导出一致，用户在
    多维表格里看到的列名即此）。值走 ``col.value(row)``（含 fmt 变换），
    保留原始标量 / list / dict——``BitableClient`` 的 ``row_to_fields`` 再按
    Bitable 单元格要求归一（list 换行拼串、dict JSON 串化、超长截断）。
    """
    records: list[dict[str, Any]] = []
    for row in rows:
        rec: dict[str, Any] = {}
        for col in columns:
            rec[col.header] = col.value(row)
        records.append(rec)
    return records


async def write_rows_to_bitable(
    *,
    app_token: str,
    table_id: str,
    rows: list[dict[str, Any]],
    columns: list[ExportColumn],
    user_access_token: str | None = None,
) -> dict[str, Any]:
    """把 rows 按列谱写入指定多维表格。返回 {created, failed, errors}。

    默认用 **app 身份**（``user_access_token=None``，SDK 自动 tenant_access_token）
    写入，要求本应用已被加为目标表协作者。仅当显式传入 user token 时才走 user
    身份（保留兼容，当前无调用点使用）。

    不抛异常给上层（除权限类由 BitableClient 直接抛，上层据此提示加协作者）；
    普通失败计入 errors，让调用方能部分成功地回显结果。
    """
    records = build_bitable_records(rows, columns)
    client = BitableClient(user_access_token)
    result = await client.batch_create_records(app_token, table_id, records)
    logger.info(
        "bitable export: table=%s created=%d failed=%d",
        table_id, result.get("created", 0), result.get("failed", 0),
    )
    return result
