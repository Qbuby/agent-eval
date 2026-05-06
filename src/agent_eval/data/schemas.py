from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from agent_eval.models.test_case import TestCase


@dataclass
class ValidationResult:
    valid: bool
    cases: list[TestCase]
    errors: list[str] = field(default_factory=list)


def validate_and_parse(data: Any) -> ValidationResult:
    if isinstance(data, dict):
        data = [data]

    if not isinstance(data, list):
        return ValidationResult(valid=False, cases=[], errors=["Input must be a JSON object or array"])

    cases: list[TestCase] = []
    errors: list[str] = []

    for i, item in enumerate(data):
        if not isinstance(item, dict):
            errors.append(f"Case #{i + 1}: expected a JSON object, got {type(item).__name__}")
            continue
        try:
            item.setdefault("dataset_version", "")
            case = TestCase(**item)
            cases.append(case)
        except (ValidationError, TypeError) as e:
            errors.append(f"Case #{i + 1}: {e}")

    return ValidationResult(valid=len(errors) == 0, cases=cases, errors=errors)
