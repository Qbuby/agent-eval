"""reset langfuse_metrics.environments to empty (= pull all environments)

Revision ID: 0040
Revises: 0039
Create Date: 2026-08-08

Langfuse 指标同步此前把目标环境写死成 ``saas-prod,xinchai-prod,smartlink-hc-dev``
（代码常量 + DEFAULT_CONFIGS 双份），拉取时逐个带 ``environment`` 过滤，于是
``langfuse_trace_metrics`` 里只可能出现这三个环境的数据，前端「环境」筛选下拉
（distinct 自该表）自然也就只有三项——这才是「环境下拉不全」的根因，不是查询层。

同步侧已改成：环境白名单留空 = 不带 ``environment`` 过滤、拉该 project 下全部
环境，每行的 environment 取 trace 自报值。但 ``init_defaults`` 只在 key 不存在
时插入，已部署库里那行旧值仍在，光改代码默认值对存量环境无效，故用本迁移清空。

只清「仍等于旧硬编码默认」的行：如果运维刻意改过这个值（想收窄拉取范围），
原样保留，不覆盖人工配置。清空后下一轮轮询即拉全部环境，新环境会陆续入库。
"""

import json

from alembic import op
import sqlalchemy as sa


revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None

_TABLE = "system_configs"
_KEY = "langfuse_metrics.environments"

# 旧硬编码默认值（升级前 DEFAULT_CONFIGS 里的那串）；只有仍是它才重置。
_LEGACY_VALUE = "saas-prod,xinchai-prod,smartlink-hc-dev"

# config 的 JSONB 形状由 config_service._pack 决定：
# {"options": [{"value": ..., "label": ...}], "default_index": N}
_EMPTY_PACKED = '{"options": [{"value": "", "label": null}], "default_index": 0}'
_LEGACY_PACKED = (
    '{"options": [{"value": "saas-prod,xinchai-prod,smartlink-hc-dev", "label": null}], '
    '"default_index": 0}'
)


def _table_exists(bind) -> bool:
    return _TABLE in set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind):
        return
    # 用 jsonb 相等比较而非文本比较，免受键序 / 空白差异影响。
    bind.execute(
        sa.text(
            f"UPDATE {_TABLE} SET value = CAST(:new AS jsonb) "
            "WHERE key = :key AND value = CAST(:legacy AS jsonb)"
        ),
        {"new": _EMPTY_PACKED, "key": _KEY, "legacy": _LEGACY_PACKED},
    )


def downgrade() -> None:
    """把留空的那行还原成旧的三环境白名单（仅当当前确为空值）。"""
    bind = op.get_bind()
    if not _table_exists(bind):
        return
    bind.execute(
        sa.text(
            f"UPDATE {_TABLE} SET value = CAST(:legacy AS jsonb) "
            "WHERE key = :key AND value = CAST(:new AS jsonb)"
        ),
        {"new": _EMPTY_PACKED, "key": _KEY, "legacy": _LEGACY_PACKED},
    )
