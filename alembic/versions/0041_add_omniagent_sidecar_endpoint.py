"""add OmniAgent same-Pod sidecar endpoint preset

Revision ID: 0041
Revises: 0040
Create Date: 2026-08-21

Docker Compose 与 Kubernetes sidecar 的网络地址不同：Compose 内由服务名
``omniagent`` 解析；同 Pod 的 backend 与 OmniAgent 共享网络命名空间，应走
``127.0.0.1``。DEFAULT_CONFIGS 只会插入不存在的 key，不能更新存量配置，故本迁移
在不改变现有选项与默认项的前提下幂等追加 sidecar 地址。
"""

from __future__ import annotations

import json
from typing import Any

from alembic import op
import sqlalchemy as sa


revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

_TABLE = "system_configs"
_KEY = "target_agent.endpoint_url"
_COMPOSE_URL = "http://omniagent:8090/api/agent/langgraph"
_COMPOSE_LABEL = "OmniAgent（Docker Compose）"
_LEGACY_COMPOSE_LABEL = "OmniAgent（系统智能体）"
_SIDECAR_URL = "http://127.0.0.1:8090/api/agent/langgraph"
_SIDECAR_LABEL = "OmniAgent（同 Pod sidecar）"


def _normalize_options(raw: Any) -> tuple[list[dict[str, Any]], int]:
    """按 config_service 的兼容口径读取历史 value 形状。"""
    if isinstance(raw, dict):
        options = raw.get("options")
        if isinstance(options, list):
            normalized: list[dict[str, Any]] = []
            for item in options:
                if isinstance(item, dict) and "value" in item:
                    normalized.append({"value": item["value"], "label": item.get("label")})
                else:
                    normalized.append({"value": item, "label": None})
            default_index = raw.get("default_index", 0)
            if not isinstance(default_index, int) or not 0 <= default_index < len(normalized):
                default_index = 0
            return normalized, default_index
        if "v" in raw:
            return [{"value": raw["v"], "label": None}], 0
    return [{"value": raw, "label": None}], 0


def _upgrade_value(raw: Any) -> dict[str, Any]:
    """规范 Compose 标签并幂等追加 sidecar 地址，不改变默认项。"""
    options, default_index = _normalize_options(raw)
    # 历史坏数据可能是空 options 袋。直接追加会让 sidecar 成为索引 0 的默认目标，
    # 违反迁移不切换默认 agent 的约定；先补空占位再追加。
    if not options:
        options.append({"value": "", "label": None})
        default_index = 0
    for item in options:
        if (
            item.get("value") == _COMPOSE_URL
            and item.get("label") in (None, _LEGACY_COMPOSE_LABEL)
        ):
            item["label"] = _COMPOSE_LABEL
    if not any(item.get("value") == _SIDECAR_URL for item in options):
        options.append({"value": _SIDECAR_URL, "label": _SIDECAR_LABEL})
    return {"options": options, "default_index": default_index}


def _downgrade_value(raw: Any) -> dict[str, Any]:
    options, default_index = _normalize_options(raw)
    # 只移除本迁移的精确 URL + label，保留运维自行配置的同 URL 不同标签项。
    kept: list[dict[str, Any]] = []
    removed_before_default = 0
    default_removed = False
    for index, item in enumerate(options):
        migration_item = (
            item.get("value") == _SIDECAR_URL
            and item.get("label") == _SIDECAR_LABEL
        )
        if migration_item:
            if index < default_index:
                removed_before_default += 1
            elif index == default_index:
                default_removed = True
            continue
        kept.append(item)

    if not kept:
        kept = [{"value": "", "label": None}]
        new_default = 0
    elif default_removed:
        new_default = 0
    else:
        new_default = max(0, default_index - removed_before_default)
        if new_default >= len(kept):
            new_default = 0

    # upgrade 会把本项目旧标签规范成 Compose 标签；downgrade 对称恢复。
    for item in kept:
        if item.get("value") == _COMPOSE_URL and item.get("label") == _COMPOSE_LABEL:
            item["label"] = _LEGACY_COMPOSE_LABEL
    return {"options": kept, "default_index": new_default}


def _table_exists(bind) -> bool:
    return _TABLE in set(sa.inspect(bind).get_table_names())


def _rewrite(transform) -> None:
    bind = op.get_bind()
    if not _table_exists(bind):
        return
    row = bind.execute(
        sa.text(f"SELECT value FROM {_TABLE} WHERE key = :key FOR UPDATE"),
        {"key": _KEY},
    ).fetchone()
    if row is None:
        return
    raw = row[0]
    updated = transform(raw)
    if updated != raw:
        bind.execute(
            sa.text(f"UPDATE {_TABLE} SET value = CAST(:value AS jsonb) WHERE key = :key"),
            {"key": _KEY, "value": json.dumps(updated, ensure_ascii=False)},
        )


def upgrade() -> None:
    _rewrite(_upgrade_value)


def downgrade() -> None:
    _rewrite(_downgrade_value)
