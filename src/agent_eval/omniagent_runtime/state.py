from __future__ import annotations

from collections.abc import Mapping


class InvalidTransition(ValueError):
    pass


JOB_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "queued": frozenset({"provisioning", "cancelled", "expired"}),
    "provisioning": frozenset({"running", "queued", "failed", "cancelled", "expired"}),
    "running": frozenset({"queued", "succeeded", "failed", "cancelled", "expired"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
    "expired": frozenset(),
}

ACTION_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "prepared": frozenset({"approved", "denied", "expired", "cancelled"}),
    "approved": frozenset({"executing", "cancelled", "expired"}),
    "executing": frozenset({"succeeded", "failed", "cancelled"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
    "expired": frozenset(),
    "denied": frozenset(),
}


def require_transition(
    transitions: Mapping[str, frozenset[str]], current: str, target: str
) -> None:
    if target not in transitions.get(current, frozenset()):
        raise InvalidTransition(f"invalid transition: {current} -> {target}")
