"""Widen users.role from VARCHAR(16) to VARCHAR(32).

Background
----------
``users.role`` was created as ``String(16)`` back in 0002. The multitenant
work (0018) introduced the ``external_customer`` role constant
(``ROLE_EXTERNAL`` in ``auth/dependencies.py``), but ``"external_customer"``
is 17 characters — one over the column limit. Any attempt to create an
external-customer account (admin 开户 API or a direct seed) fails with
``StringDataRightTruncationError: value too long for type character
varying(16)``, which silently blocks the entire external-customer portal.

This migration widens the column to 32 chars so all current and reserved
role names fit. No data backfill needed — widening never truncates existing
rows.
"""
from alembic import op
import sqlalchemy as sa


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "users",
        "role",
        existing_type=sa.String(length=16),
        type_=sa.String(length=32),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Reversible only if no row holds a value longer than 16 chars
    # (e.g. external_customer). Callers must clean those up first.
    op.alter_column(
        "users",
        "role",
        existing_type=sa.String(length=32),
        type_=sa.String(length=16),
        existing_nullable=False,
    )
