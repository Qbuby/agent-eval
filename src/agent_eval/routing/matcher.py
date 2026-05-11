from __future__ import annotations

import fnmatch
from datetime import datetime
from typing import Any


class RuleMatcher:
    def matches(self, rule: Any, run: dict, project_name: str) -> bool:
        if not self._match_project(rule.source_project, project_name):
            return False

        conditions = rule.conditions or {}

        if not self._match_tags(conditions.get("tags"), run):
            return False

        if not self._match_metadata(conditions.get("metadata_match"), run):
            return False

        if not self._match_status(conditions.get("status"), run):
            return False

        if not self._match_duration(conditions.get("min_duration_ms"), run):
            return False

        return True

    def _match_project(self, pattern: str, project_name: str) -> bool:
        return fnmatch.fnmatch(project_name, pattern)

    def _match_tags(self, required_tags: list[str] | None, run: dict) -> bool:
        if not required_tags:
            return True
        run_tags = set(run.get("tags") or [])
        return all(tag in run_tags for tag in required_tags)

    def _match_metadata(self, metadata_match: dict | None, run: dict) -> bool:
        if not metadata_match:
            return True
        run_metadata = run.get("extra", {}).get("metadata", {})
        if not run_metadata and run.get("metadata"):
            run_metadata = run["metadata"]
        return all(
            run_metadata.get(k) == v for k, v in metadata_match.items()
        )

    def _match_status(self, status_filter: str | None, run: dict) -> bool:
        if not status_filter or status_filter == "all":
            return True
        run_status = run.get("status", "")
        return run_status == status_filter

    def _match_duration(self, min_duration_ms: int | None, run: dict) -> bool:
        if min_duration_ms is None:
            return True
        start = run.get("start_time")
        end = run.get("end_time")
        if not start or not end:
            return False
        if isinstance(start, str):
            start = datetime.fromisoformat(start.replace("Z", "+00:00"))
            end = datetime.fromisoformat(end.replace("Z", "+00:00"))
        duration_ms = (end - start).total_seconds() * 1000
        return duration_ms >= min_duration_ms
