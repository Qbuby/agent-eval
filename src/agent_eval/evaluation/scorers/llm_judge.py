from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

JUDGE_SYSTEM_PROMPT = """你是一个专业的 AI 回答质量评估专家。你需要根据给定的评分维度，对 AI 助手的回答进行评分。

评分规则：
- 每个维度打 1-10 分（1=极差，5=一般，10=完美）
- 给出简短的评分理由

请严格按照以下 JSON 格式输出：
```json
{
  "scores": [
    {"dimension": "维度名", "score": 分数, "reason": "理由"}
  ]
}
```"""

JUDGE_USER_TEMPLATE = """## 用户问题
{question}

## AI 回答
{answer}

## 评分维度
{dimensions}

请对以上回答进行评分。"""


@dataclass
class JudgeDimension:
    name: str
    weight: float = 1.0
    description: str = ""


@dataclass
class DimensionResult:
    dimension: str
    score: float
    reason: str = ""


@dataclass
class JudgeResult:
    dimensions: list[DimensionResult] = field(default_factory=list)
    aggregate_score: float = 0.0
    raw_response: str = ""

    @property
    def passed(self) -> bool:
        return self.aggregate_score >= 7.0


class LLMJudgeScorer:
    """LLM-as-Judge scorer that evaluates agent responses across configurable dimensions."""

    def __init__(
        self,
        llm: ChatOpenAI | None = None,
        dimensions: list[JudgeDimension] | None = None,
        system_prompt: str | None = None,
        user_template: str | None = None,
    ):
        self.llm = llm
        self.dimensions = dimensions or [
            JudgeDimension(name="准确性", weight=0.4, description="回答是否准确、事实正确"),
            JudgeDimension(name="完整性", weight=0.3, description="回答是否完整覆盖问题要点"),
            JudgeDimension(name="相关性", weight=0.3, description="回答是否与问题相关、不跑题"),
        ]
        self.system_prompt = system_prompt or JUDGE_SYSTEM_PROMPT
        self.user_template = user_template or JUDGE_USER_TEMPLATE

    async def score(self, question: str, answer: str) -> JudgeResult:
        if not answer or answer.startswith("[错误]") or answer.startswith("[超时]"):
            return JudgeResult(
                dimensions=[
                    DimensionResult(dimension=d.name, score=0, reason="无有效回答")
                    for d in self.dimensions
                ],
                aggregate_score=0.0,
            )

        dimensions_text = "\n".join(
            f"- {d.name}: {d.description}" for d in self.dimensions
        )

        user_msg = self.user_template.format(
            question=question, answer=answer, dimensions=dimensions_text,
        )

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_msg},
        ]

        try:
            resp = await self.llm.ainvoke(messages)
            raw = resp.content
            return self._parse_response(raw)
        except Exception as e:
            logger.error("LLM Judge failed: %s", e)
            return JudgeResult(
                dimensions=[
                    DimensionResult(dimension=d.name, score=5, reason=f"评分失败: {e}")
                    for d in self.dimensions
                ],
                aggregate_score=5.0,
                raw_response=str(e),
            )

    def _parse_response(self, raw: str) -> JudgeResult:
        json_match = re.search(r"\{[\s\S]*\}", raw)
        if not json_match:
            logger.warning("Could not parse judge response as JSON")
            return JudgeResult(aggregate_score=5.0, raw_response=raw)

        try:
            data = json.loads(json_match.group())
        except json.JSONDecodeError:
            return JudgeResult(aggregate_score=5.0, raw_response=raw)

        scores_raw = data.get("scores", [])
        dim_results: list[DimensionResult] = []
        for item in scores_raw:
            dim_results.append(DimensionResult(
                dimension=item.get("dimension", ""),
                score=float(item.get("score", 5)),
                reason=item.get("reason", ""),
            ))

        total_weight = sum(d.weight for d in self.dimensions)
        weighted_sum = 0.0
        for dim_cfg in self.dimensions:
            matched = next((r for r in dim_results if r.dimension == dim_cfg.name), None)
            if matched:
                weighted_sum += matched.score * dim_cfg.weight
            else:
                weighted_sum += 5.0 * dim_cfg.weight

        aggregate = weighted_sum / total_weight if total_weight > 0 else 5.0

        return JudgeResult(
            dimensions=dim_results,
            aggregate_score=aggregate,
            raw_response=raw,
        )
