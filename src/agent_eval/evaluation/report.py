from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class CaseResult:
    case_id: str
    question: str
    answer: str
    latency_ms: float
    scores: dict[str, float] = field(default_factory=dict)
    aggregate_score: float = 0.0
    reasons: dict[str, str] = field(default_factory=dict)
    error: str | None = None


@dataclass
class BenchReport:
    model_name: str = ""
    dataset: str = ""
    run_time: str = field(default_factory=lambda: datetime.now().isoformat())
    total_cases: int = 0
    avg_score: float = 0.0
    pass_rate: float = 0.0
    avg_latency_ms: float = 0.0
    dimension_averages: dict[str, float] = field(default_factory=dict)
    results: list[CaseResult] = field(default_factory=list)

    def compute_summary(self) -> None:
        if not self.results:
            return
        self.total_cases = len(self.results)
        valid = [r for r in self.results if r.error is None]
        if not valid:
            return

        self.avg_score = statistics.mean(r.aggregate_score for r in valid)
        self.pass_rate = sum(1 for r in valid if r.aggregate_score >= 7.0) / len(valid)
        self.avg_latency_ms = statistics.mean(r.latency_ms for r in valid)

        all_dims: dict[str, list[float]] = {}
        for r in valid:
            for dim, score in r.scores.items():
                all_dims.setdefault(dim, []).append(score)
        self.dimension_averages = {
            dim: statistics.mean(scores) for dim, scores in all_dims.items()
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "dataset": self.dataset,
            "run_time": self.run_time,
            "total_cases": self.total_cases,
            "avg_score": round(self.avg_score, 2),
            "pass_rate": round(self.pass_rate, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "dimension_averages": {k: round(v, 2) for k, v in self.dimension_averages.items()},
            "results": [
                {
                    "case_id": r.case_id,
                    "question": r.question,
                    "answer": r.answer[:500],
                    "latency_ms": round(r.latency_ms, 1),
                    "scores": {k: round(v, 1) for k, v in r.scores.items()},
                    "aggregate_score": round(r.aggregate_score, 2),
                    "reasons": r.reasons,
                    "error": r.error,
                }
                for r in self.results
            ],
        }

    def save(self, output_dir: str | Path) -> Path:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = out / f"report_{ts}.json"
        report_path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        jsonl_path = out / f"details_{ts}.jsonl"
        with open(jsonl_path, "w", encoding="utf-8") as f:
            meta = {"__meta__": True, "model": self.model_name, "run_time": self.run_time, "total": self.total_cases}
            f.write(json.dumps(meta, ensure_ascii=False) + "\n")
            for r in self.results:
                line = {
                    "case_id": r.case_id,
                    "question": r.question,
                    "answer": r.answer,
                    "latency_ms": round(r.latency_ms, 1),
                    "scores": r.scores,
                    "aggregate_score": round(r.aggregate_score, 2),
                    "reasons": r.reasons,
                    "error": r.error,
                }
                f.write(json.dumps(line, ensure_ascii=False) + "\n")

        return report_path
