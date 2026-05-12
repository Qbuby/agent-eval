from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ValidationSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass
class ValidationIssue:
    field: str
    message: str
    severity: ValidationSeverity = ValidationSeverity.ERROR


@dataclass
class ValidationResult:
    valid: bool = True
    issues: list[ValidationIssue] = field(default_factory=list)
    needs_review: bool = False

    def add_error(self, field_name: str, message: str) -> None:
        self.issues.append(ValidationIssue(field=field_name, message=message, severity=ValidationSeverity.ERROR))
        self.valid = False
        self.needs_review = True

    def add_warning(self, field_name: str, message: str) -> None:
        self.issues.append(ValidationIssue(field=field_name, message=message, severity=ValidationSeverity.WARNING))


@dataclass
class QualityReport:
    total: int = 0
    valid: int = 0
    needs_review: int = 0
    issues_by_field: dict[str, int] = field(default_factory=dict)
    results: list[dict[str, Any]] = field(default_factory=list)


class ExampleValidator:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.require_expected_output = self.config.get("require_expected_output", False)
        self.max_messages = self.config.get("max_messages", 100)
        self.allowed_roles = self.config.get("allowed_roles", ["system", "user", "assistant", "tool"])

    def validate_example(self, example: dict[str, Any]) -> ValidationResult:
        result = ValidationResult()

        self._validate_input_messages(example, result)
        self._validate_expected_output(example, result)
        self._validate_tool_calls(example, result)
        self._validate_metadata(example, result)

        return result

    def _validate_input_messages(self, example: dict[str, Any], result: ValidationResult) -> None:
        messages = example.get("input_messages")

        if messages is None:
            result.add_error("input_messages", "input_messages is required")
            return

        if not isinstance(messages, list):
            result.add_error("input_messages", "input_messages must be a list")
            return

        if len(messages) == 0:
            result.add_error("input_messages", "input_messages must not be empty")
            return

        if len(messages) > self.max_messages:
            result.add_warning("input_messages", f"message count ({len(messages)}) exceeds recommended max ({self.max_messages})")

        for i, msg in enumerate(messages):
            if not isinstance(msg, dict):
                result.add_error("input_messages", f"message at index {i} must be a dict")
                continue

            if "role" not in msg:
                result.add_error("input_messages", f"message at index {i} missing 'role'")
            elif msg["role"] not in self.allowed_roles:
                result.add_warning("input_messages", f"message at index {i} has unexpected role '{msg['role']}'")

            if "content" not in msg:
                result.add_error("input_messages", f"message at index {i} missing 'content'")

    def _validate_expected_output(self, example: dict[str, Any], result: ValidationResult) -> None:
        if self.require_expected_output:
            expected = example.get("expected_output")
            criteria = example.get("expected_output_criteria", [])
            if not expected and not criteria:
                result.add_error("expected_output", "expected_output or expected_output_criteria is required")

    def _validate_tool_calls(self, example: dict[str, Any], result: ValidationResult) -> None:
        tool_calls = example.get("expected_tool_calls")
        if tool_calls is None:
            return

        if not isinstance(tool_calls, list):
            result.add_error("expected_tool_calls", "expected_tool_calls must be a list")
            return

        for i, call in enumerate(tool_calls):
            if not isinstance(call, dict):
                result.add_error("expected_tool_calls", f"tool call at index {i} must be a dict")
                continue
            if "name" not in call:
                result.add_error("expected_tool_calls", f"tool call at index {i} missing 'name'")

    def _validate_metadata(self, example: dict[str, Any], result: ValidationResult) -> None:
        name = example.get("name")
        if not name or not isinstance(name, str) or not name.strip():
            result.add_warning("name", "example should have a non-empty name")

    def validate_batch(self, examples: list[dict[str, Any]]) -> QualityReport:
        report = QualityReport(total=len(examples))

        for example in examples:
            vr = self.validate_example(example)
            if vr.valid:
                report.valid += 1
            if vr.needs_review:
                report.needs_review += 1

            for issue in vr.issues:
                report.issues_by_field[issue.field] = report.issues_by_field.get(issue.field, 0) + 1

            report.results.append({
                "name": example.get("name", ""),
                "valid": vr.valid,
                "needs_review": vr.needs_review,
                "issues": [{"field": i.field, "message": i.message, "severity": i.severity.value} for i in vr.issues],
            })

        return report
