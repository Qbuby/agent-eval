#!/usr/bin/env python3
"""将镜像内置的评估器定义幂等同步到目标数据库。

该脚本是 Docker Compose ``migrate`` 一次性任务的数据同步步骤，不属于 Alembic
schema revision。默认执行 apply；可显式执行 revert 回滚本次同步仍在生效的改动：

    python /app/scripts/sync_evaluator_reference_criteria.py apply
    python /app/scripts/sync_evaluator_reference_criteria.py revert

已有目标评估器只修改关键点数据源映射，并保留目标环境的 provider、model 与其他
参数；缺失目标评估器才从 provider-neutral 定义文件完整创建。每次实际修改都追加
``evaluator_versions`` 快照，并在独立备份表中记录精确回滚信息。
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import re
import uuid

import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.dialects.postgresql import JSONB, UUID


MIGRATION_KEY = "evaluator-reference-criteria-v1"
TARGET_SOURCE = "reference_criteria"
OLD_SOURCE = "metadata.turn_criteria"
VERSION_DESCRIPTION = f"data migration: {MIGRATION_KEY}"
INTERNAL_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
BACKUP_TABLE_NAME = "evaluator_data_migration_backups"
ADVISORY_LOCK_ID = 72_182
DEFAULT_DEFINITIONS_PATH = (
    Path(__file__).resolve().parent.parent
    / "deploy"
    / "evaluator-migrations"
    / "reference-criteria-v1.json"
)

MULTITURN_NAMES = {
    "多轮-任务完成度",
    "多轮-任务完成度对比",
    "多轮-回答正确性",
    "多轮-回答正确性对比",
    "多轮-安全与拒答恰当性",
    "多轮-安全与拒答恰当性对比",
    "多轮-对话连贯性",
    "多轮-对话连贯性对比",
    "多轮-工具调用正确性",
    "多轮-工具调用正确性对比",
    "多轮-指令遵循",
    "多轮-指令遵循对比",
}

NON_MULTITURN_CRITERIA_LINE = {
    "幻觉率/agent": "Reference criteria (must check one by one): {{Criteria}}",
    "幻觉率/llm-judge": "Reference criteria (must check one by one): {{Criteria}}",
    "正确性/agent": "Reference criteria (must check one by one): {{Criteria}}",
    "正确性/llm-judge": "Reference criteria (must check one by one): {{Criteria}}",
    "简洁度/agent": "Reference criteria (must check one by one): {{Criteria}}",
    "简洁度/llm-judge": "Reference criteria (must check one by one): {{Criteria}}",
    "幻觉率对比/llm-judge": "参考要点（须逐条核对）：{{Criteria}}",
    "正确性对比/llm-judge": "参考要点（须逐条核对）：{{Criteria}}",
    "简洁度对比/llm-judge": "参考要点（须逐条核对）：{{Criteria}}",
}

TARGET_NAMES = MULTITURN_NAMES | set(NON_MULTITURN_CRITERIA_LINE)
MUSTACHE_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def _database_url() -> str:
    """使用与 Alembic 一致的同步数据库连接配置。"""
    host = os.getenv("DB_HOST")
    if host:
        return sa.URL.create(
            "postgresql+psycopg2",
            username=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", "postgres"),
            host=host,
            port=int(os.getenv("DB_PORT", "5432")),
            database=os.getenv("DB_NAME", "agent_eval"),
        ).render_as_string(hide_password=False)

    config_path = Path(os.getenv("ALEMBIC_CONFIG", "alembic.ini"))
    config = Config(str(config_path))
    url = config.get_main_option("sqlalchemy.url") or ""
    if not url:
        raise RuntimeError("数据库连接未配置：缺少 DB_HOST 且 alembic.ini 无 sqlalchemy.url")
    return url.replace("+asyncpg", "+psycopg2")


def _load_definitions(path: Path) -> dict[str, dict]:
    """读取并严格校验镜像内置的 21 个 provider-neutral 定义。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise RuntimeError("评估器定义文件必须是 JSON 数组")

    definitions: dict[str, dict] = {}
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise RuntimeError("评估器定义项结构无效")
        name = item["name"]
        if name in definitions:
            raise RuntimeError(f"评估器定义重名：{name}")
        params = item.get("params")
        if not isinstance(params, dict) or "provider_id" in params:
            raise RuntimeError(f"评估器定义不是 provider-neutral：{name}")
        mapping = params.get("variable_mapping")
        if not isinstance(mapping, dict) or TARGET_SOURCE not in mapping.values():
            raise RuntimeError(f"评估器定义未映射 reference_criteria：{name}")
        provider_name = item.get("provider_name")
        if provider_name not in {"kiro", "target-agent-sse"}:
            raise RuntimeError(f"评估器定义包含未知 provider：{name}")
        definitions[name] = item

    if set(definitions) != TARGET_NAMES:
        missing = sorted(TARGET_NAMES - set(definitions))
        extra = sorted(set(definitions) - TARGET_NAMES)
        raise RuntimeError(f"评估器定义集合不匹配：missing={missing}, extra={extra}")
    return definitions


def _plan_multiturn(name: str, params: dict) -> dict | None:
    mapping = dict(params.get("variable_mapping") or {})
    expected_key = "Criteria" if name.endswith("对比") else "Checklist"
    old_keys = [key for key, source in mapping.items() if source == OLD_SOURCE]
    if mapping.get(expected_key) == TARGET_SOURCE and not old_keys:
        return None

    for key in old_keys:
        mapping[key] = TARGET_SOURCE
    mapping[expected_key] = TARGET_SOURCE
    new_params = copy.deepcopy(params)
    new_params["variable_mapping"] = mapping
    return new_params


def _plan_non_multiturn(name: str, params: dict) -> dict | None:
    mapping = dict(params.get("variable_mapping") or {})
    prompt = params.get("evaluation_prompt") or ""
    mapping_ok = mapping.get("Criteria") == TARGET_SOURCE
    prompt_ok = "Criteria" in set(MUSTACHE_RE.findall(prompt))
    if mapping_ok and prompt_ok:
        return None

    mapping["Criteria"] = TARGET_SOURCE
    if not prompt_ok:
        prompt = prompt.rstrip("\n") + "\n" + NON_MULTITURN_CRITERIA_LINE[name]

    new_params = copy.deepcopy(params)
    new_params["variable_mapping"] = mapping
    new_params["evaluation_prompt"] = prompt
    return new_params


def _plan_for(name: str, params: dict) -> dict | None:
    if name in MULTITURN_NAMES:
        return _plan_multiturn(name, params)
    return _plan_non_multiturn(name, params)


def _require_columns(inspector: sa.Inspector, table: str, required: set[str]) -> None:
    if table not in inspector.get_table_names():
        raise RuntimeError(f"数据同步要求表存在：{table}")
    actual = {column["name"] for column in inspector.get_columns(table)}
    missing = required - actual
    if missing:
        raise RuntimeError(f"数据同步要求 {table} 包含列：{sorted(missing)}")


def _ensure_backup_table(connection: sa.Connection) -> sa.Table:
    metadata = sa.MetaData()
    inspector = sa.inspect(connection)
    if BACKUP_TABLE_NAME not in inspector.get_table_names():
        backup = sa.Table(
            BACKUP_TABLE_NAME,
            metadata,
            sa.Column("migration_key", sa.String(128), primary_key=True),
            sa.Column("evaluator_name", sa.String(128), primary_key=True),
            sa.Column("evaluator_id", UUID(as_uuid=True), nullable=False),
            sa.Column("action", sa.String(16), nullable=False),
            sa.Column("old_params", JSONB(), nullable=True),
            sa.Column("old_current_version_id", UUID(as_uuid=True), nullable=True),
            sa.Column("applied_params", JSONB(), nullable=False),
            sa.Column("applied_version_id", UUID(as_uuid=True), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        metadata.create_all(connection, tables=[backup])
        return backup
    return sa.Table(BACKUP_TABLE_NAME, metadata, autoload_with=connection)


def _reflect_tables(connection: sa.Connection) -> tuple[sa.Table, sa.Table, sa.Table]:
    inspector = sa.inspect(connection)
    _require_columns(
        inspector,
        "evaluator_configs",
        {
            "id",
            "name",
            "evaluator_type",
            "description",
            "params",
            "is_active",
            "tag",
            "current_version_id",
            "tenant_id",
            "updated_at",
        },
    )
    _require_columns(
        inspector,
        "evaluator_versions",
        {"id", "evaluator_id", "version_number", "params", "description", "tenant_id"},
    )
    _require_columns(inspector, "evaluator_providers", {"id", "name"})
    metadata = sa.MetaData()
    return (
        sa.Table("evaluator_configs", metadata, autoload_with=connection),
        sa.Table("evaluator_versions", metadata, autoload_with=connection),
        sa.Table("evaluator_providers", metadata, autoload_with=connection),
    )


def _next_version_number(
    connection: sa.Connection,
    versions: sa.Table,
    evaluator_id: uuid.UUID,
) -> int:
    latest = connection.execute(
        sa.select(sa.func.max(versions.c.version_number)).where(
            versions.c.evaluator_id == evaluator_id
        )
    ).scalar_one()
    return (latest or 0) + 1


def _apply(connection: sa.Connection, definitions: dict[str, dict]) -> dict:
    configs, versions, providers = _reflect_tables(connection)
    backups = _ensure_backup_table(connection)

    provider_names = sorted({item["provider_name"] for item in definitions.values()})
    provider_ids = {
        row["name"]: row["id"]
        for row in connection.execute(
            sa.select(providers.c.id, providers.c.name).where(
                providers.c.name.in_(provider_names)
            )
        ).mappings()
    }

    existing_rows = connection.execute(
        sa.select(configs).where(configs.c.name.in_(sorted(TARGET_NAMES))).with_for_update()
    ).mappings().all()
    wrong_types = sorted(
        row["name"] for row in existing_rows if row["evaluator_type"] != "configurable_judge"
    )
    if wrong_types:
        raise RuntimeError(
            "目标名称已被非 configurable_judge 评估器占用：" + "、".join(wrong_types)
        )
    existing = {row["name"]: row for row in existing_rows}

    backup_rows = connection.execute(
        sa.select(backups).where(backups.c.migration_key == MIGRATION_KEY)
    ).mappings().all()
    backup_by_name = {row["evaluator_name"]: row for row in backup_rows}

    stats: dict[str, object] = {
        "migration_key": MIGRATION_KEY,
        "created": 0,
        "updated": 0,
        "already_ok": 0,
        "managed": 0,
        "preserved_after_edit": 0,
        "missing_providers": [],
    }
    missing_providers: set[str] = set()

    for name in sorted(TARGET_NAMES):
        row = existing.get(name)
        prior = backup_by_name.get(name)
        if prior is not None:
            if row is not None and row["id"] == prior["evaluator_id"]:
                unchanged = (
                    row["current_version_id"] == prior["applied_version_id"]
                    and row["params"] == prior["applied_params"]
                )
                key = "managed" if unchanged else "preserved_after_edit"
                stats[key] = int(stats[key]) + 1
            else:
                stats["preserved_after_edit"] = int(stats["preserved_after_edit"]) + 1
            continue

        if row is not None:
            current_params = copy.deepcopy(row["params"] or {})
            new_params = _plan_for(name, current_params)
            if new_params is None:
                stats["already_ok"] = int(stats["already_ok"]) + 1
                continue

            version_id = uuid.uuid4()
            connection.execute(
                backups.insert().values(
                    migration_key=MIGRATION_KEY,
                    evaluator_name=name,
                    evaluator_id=row["id"],
                    action="updated",
                    old_params=current_params,
                    old_current_version_id=row["current_version_id"],
                    applied_params=new_params,
                    applied_version_id=version_id,
                )
            )
            connection.execute(
                versions.insert().values(
                    id=version_id,
                    evaluator_id=row["id"],
                    version_number=_next_version_number(connection, versions, row["id"]),
                    params=new_params,
                    description=VERSION_DESCRIPTION,
                    tenant_id=row["tenant_id"],
                )
            )
            connection.execute(
                configs.update()
                .where(configs.c.id == row["id"])
                .values(
                    params=new_params,
                    current_version_id=version_id,
                    updated_at=sa.func.now(),
                )
            )
            stats["updated"] = int(stats["updated"]) + 1
            continue

        definition = definitions[name]
        params = copy.deepcopy(definition["params"])
        provider_name = definition["provider_name"]
        provider_id = provider_ids.get(provider_name)
        if provider_id is not None:
            params["provider_id"] = str(provider_id)
        else:
            missing_providers.add(provider_name)

        evaluator_id = uuid.uuid4()
        version_id = uuid.uuid4()
        connection.execute(
            configs.insert().values(
                id=evaluator_id,
                name=name,
                evaluator_type="configurable_judge",
                description=definition.get("description"),
                params=params,
                is_active=bool(definition.get("is_active", True)),
                tag=definition.get("tag") or name,
                current_version_id=None,
                tenant_id=INTERNAL_TENANT_ID,
            )
        )
        connection.execute(
            versions.insert().values(
                id=version_id,
                evaluator_id=evaluator_id,
                version_number=1,
                params=params,
                description=VERSION_DESCRIPTION,
                tenant_id=INTERNAL_TENANT_ID,
            )
        )
        connection.execute(
            configs.update()
            .where(configs.c.id == evaluator_id)
            .values(current_version_id=version_id, updated_at=sa.func.now())
        )
        connection.execute(
            backups.insert().values(
                migration_key=MIGRATION_KEY,
                evaluator_name=name,
                evaluator_id=evaluator_id,
                action="created",
                old_params=None,
                old_current_version_id=None,
                applied_params=params,
                applied_version_id=version_id,
            )
        )
        stats["created"] = int(stats["created"]) + 1

    stats["missing_providers"] = sorted(missing_providers)
    return stats


def _revert(connection: sa.Connection) -> dict:
    configs, versions, _providers = _reflect_tables(connection)
    backups = _ensure_backup_table(connection)
    backup_rows = connection.execute(
        sa.select(backups)
        .where(backups.c.migration_key == MIGRATION_KEY)
        .order_by(backups.c.evaluator_name)
        .with_for_update()
    ).mappings().all()

    stats = {
        "migration_key": MIGRATION_KEY,
        "restored": 0,
        "removed": 0,
        "already_absent": 0,
        "preserved_after_edit": 0,
    }
    for backup in backup_rows:
        row = connection.execute(
            sa.select(configs)
            .where(configs.c.id == backup["evaluator_id"])
            .with_for_update()
        ).mappings().one_or_none()

        if row is None:
            if backup["action"] == "created":
                connection.execute(
                    backups.delete().where(
                        backups.c.migration_key == MIGRATION_KEY,
                        backups.c.evaluator_name == backup["evaluator_name"],
                    )
                )
                stats["already_absent"] += 1
            else:
                stats["preserved_after_edit"] += 1
            continue

        unchanged = (
            row["current_version_id"] == backup["applied_version_id"]
            and row["params"] == backup["applied_params"]
        )
        if not unchanged:
            stats["preserved_after_edit"] += 1
            continue

        if backup["action"] == "created":
            # 先断开 config → current version 环，随后 evaluator_id 级联删除版本。
            connection.execute(
                configs.update()
                .where(configs.c.id == backup["evaluator_id"])
                .values(current_version_id=None)
            )
            connection.execute(configs.delete().where(configs.c.id == backup["evaluator_id"]))
            stats["removed"] += 1
        elif backup["action"] == "updated":
            connection.execute(
                configs.update()
                .where(configs.c.id == backup["evaluator_id"])
                .values(
                    params=backup["old_params"],
                    current_version_id=backup["old_current_version_id"],
                    updated_at=sa.func.now(),
                )
            )
            connection.execute(
                versions.delete().where(versions.c.id == backup["applied_version_id"])
            )
            stats["restored"] += 1
        else:
            raise RuntimeError(f"未知备份动作：{backup['action']}")

        connection.execute(
            backups.delete().where(
                backups.c.migration_key == MIGRATION_KEY,
                backups.c.evaluator_name == backup["evaluator_name"],
            )
        )

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=("apply", "revert"), default="apply")
    parser.add_argument(
        "--definitions",
        type=Path,
        default=Path(os.getenv("EVALUATOR_MIGRATION_FILE", DEFAULT_DEFINITIONS_PATH)),
    )
    args = parser.parse_args()

    definitions = _load_definitions(args.definitions) if args.action == "apply" else {}
    engine = sa.create_engine(_database_url(), poolclass=sa.pool.NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": ADVISORY_LOCK_ID},
            )
            result = _apply(connection, definitions) if args.action == "apply" else _revert(connection)
    finally:
        engine.dispose()

    print(json.dumps({"ok": True, "action": args.action, **result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
