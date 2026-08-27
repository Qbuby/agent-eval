from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class EffectivePolicy:
    axi_calls_per_turn: int = 40
    data_queries_per_turn: int = 8
    subagents_per_turn: int = 3
    model_tokens_per_turn: int = 100_000
    foreground_seconds: int = 300
    analysis_concurrency_user: int = 1
    analysis_jobs_per_day_user: int = 20
    analysis_concurrency_tenant: int = 4
    enabled_schedules_user: int = 20
    artifact_file_bytes: int = 50 * 1024 * 1024
    artifact_job_output_bytes: int = 200 * 1024 * 1024
    artifact_storage_user_bytes: int = 2 * 1024 * 1024 * 1024
    memories_user: int = 200
    memory_bytes_user: int = 256 * 1024

    def budgets(self) -> dict[str, int]:
        return {
            "axi_calls": self.axi_calls_per_turn,
            "data_queries": self.data_queries_per_turn,
            "subagents": self.subagents_per_turn,
            "model_tokens": self.model_tokens_per_turn,
            "foreground_seconds": self.foreground_seconds,
        }


DEFAULT_POLICY = EffectivePolicy()


def clamp_budgets(
    requested: dict[str, Any] | None,
    policy: EffectivePolicy = DEFAULT_POLICY,
) -> dict[str, int]:
    """Return token budgets that can only be stricter than server policy."""
    limits = policy.budgets()
    if not requested:
        return limits
    result: dict[str, int] = {}
    for key, maximum in limits.items():
        raw = requested.get(key, maximum)
        if isinstance(raw, bool):
            raw = int(raw)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = maximum
        result[key] = max(0, min(value, maximum))
    return result


def policy_dict(policy: EffectivePolicy = DEFAULT_POLICY) -> dict[str, int]:
    return {key: int(value) for key, value in asdict(policy).items()}
