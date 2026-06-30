"""Drop legacy ``llm_judge`` evaluator rows.

The 5-dimension ``llm_judge`` runner (``_evaluator_llm_judge``) was removed
from ``BUILTIN_EVALUATORS`` when we collapsed the LLM-judge family to a
single configurable evaluator (``configurable_judge``). Existing rows with
``evaluator_type='llm_judge'`` no longer have a runner — they would be
silently skipped by ``langfuse_runner`` and confuse the editor (the form
no longer renders the old dimension fields).

Cleanup strategy:
* Delete every ``evaluator_configs`` row with ``evaluator_type='llm_judge'``.
* ``evaluator_versions.evaluator_id`` has ``ON DELETE CASCADE`` (see
  migration 0014), so version snapshots and ``current_version_id`` are
  cleaned up automatically.
* Historical ``test_runs.evaluator_configs`` JSON entries that pinned to
  these evaluator ids stay as-is — runs are read-only history; the runner
  already tolerates unknown ``evaluator_type`` (skips the local-scoring
  loop).

We keep ``exact_match`` and ``tool_sequence_match`` rows: those runners are
still registered in ``BUILTIN_EVALUATORS`` as of 2026-05-28.

Downgrade is a no-op — we cannot reconstruct deleted rows. The migration
is intentionally one-way; pin the previous schema by reverting code to
0014 if a rollback is required.
"""
from alembic import op


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM evaluator_configs WHERE evaluator_type = 'llm_judge'"
    )


def downgrade() -> None:
    # Deleted rows cannot be reconstructed; downgrade is intentionally a no-op.
    pass
