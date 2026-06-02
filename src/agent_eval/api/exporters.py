"""Shared list-export helpers.

Turns a ``list[dict]`` + column spec into a downloadable ``Response`` in one
of three formats (csv / json / xlsx). Used by the evaluation, traces,
candidates and benchmark routers so every "导出" button behaves the same.

- csv  : UTF-8 with a BOM so Excel opens 中文 without mojibake.
- json : pretty-printed, ``ensure_ascii=False``; keeps nested structures intact.
- xlsx : openpyxl (already a dependency); bold + frozen header row.

Nested cell values (lists / dicts) are JSON-stringified for csv/xlsx; the
json format keeps the raw rows untouched.
"""
from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from fastapi import HTTPException
from fastapi.responses import Response

# Formats we accept on the ``?format=`` query / body field.
ExportFormat = str  # "csv" | "json" | "xlsx"
VALID_FORMATS = ("csv", "json", "xlsx")

_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# Excel hard-caps a cell at 32767 chars; trim well under that to stay safe.
_XLSX_CELL_LIMIT = 32000


@dataclass
class ExportColumn:
    """One output column.

    ``key`` indexes into the row dict; ``header`` is the human label. ``fmt``
    optionally transforms the raw value before serialization (e.g. round a
    float, join a list). When ``fmt`` is None the value is passed through and,
    for csv/xlsx, scalarized by :func:`_scalarize`.
    """
    key: str
    header: str
    fmt: Callable[[Any], Any] | None = None

    def value(self, row: dict[str, Any]) -> Any:
        raw = row.get(self.key)
        return self.fmt(raw) if self.fmt is not None else raw


def validate_format(fmt: str) -> str:
    if fmt not in VALID_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported export format '{fmt}'; expected one of {VALID_FORMATS}",
        )
    return fmt


def _scalarize(value: Any) -> str:
    """Flatten a cell value to a string for csv/xlsx output."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    # lists / dicts → compact JSON so nothing is silently dropped
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def build_export_response(
    rows: list[dict[str, Any]],
    columns: list[ExportColumn],
    fmt: str,
    filename_base: str,
) -> Response:
    """Serialize ``rows`` to ``fmt`` and wrap in an attachment Response.

    ``filename_base`` must be ASCII-safe (id / timestamp / english label) so we
    don't have to deal with RFC 5987 filename encoding.
    """
    validate_format(fmt)

    if fmt == "json":
        # JSON keeps full fidelity: emit the projected columns as-is (nested
        # structures preserved), keyed by header for readability.
        payload = [
            {col.header: col.value(row) for col in columns}
            for row in rows
        ]
        body = json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default)
        return Response(
            # errors="replace": a lone surrogate (e.g. from a half-decoded
            # upstream preview) would otherwise raise UnicodeEncodeError and
            # 500 the whole export. Replace it rather than fail the download.
            content=body.encode("utf-8", errors="replace"),
            media_type="application/json",
            headers=_disposition(filename_base, "json"),
        )

    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([col.header for col in columns])
        for row in rows:
            writer.writerow([_scalarize(col.value(row)) for col in columns])
        # Prepend BOM so Excel detects UTF-8.
        body = "﻿" + buf.getvalue()
        return Response(
            # errors="replace": guard against a lone surrogate slipping through
            # (see the json branch) — replace rather than 500 the download.
            content=body.encode("utf-8", errors="replace"),
            media_type="text/csv; charset=utf-8",
            headers=_disposition(filename_base, "csv"),
        )

    # xlsx
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Export"
    ws.append([col.header for col in columns])
    bold = Font(bold=True)
    for cell in ws[1]:
        cell.font = bold
    ws.freeze_panes = "A2"
    for row in rows:
        cells = []
        for col in columns:
            text = _scalarize(col.value(row))
            if len(text) > _XLSX_CELL_LIMIT:
                text = text[:_XLSX_CELL_LIMIT] + "…(truncated)"
            cells.append(text)
        ws.append(cells)
    out = io.BytesIO()
    wb.save(out)
    return Response(
        content=out.getvalue(),
        media_type=_XLSX_MEDIA,
        headers=_disposition(filename_base, "xlsx"),
    )


def _disposition(filename_base: str, ext: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{filename_base}.{ext}"'}
