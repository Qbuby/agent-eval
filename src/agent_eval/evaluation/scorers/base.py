from __future__ import annotations

from abc import ABC, abstractmethod

from agent_eval.models.score import DimensionScore
from agent_eval.models.test_case import TestCase
from agent_eval.models.trace import AgentTrace


class DimensionScorer(ABC):
    dimension: str = ""

    @abstractmethod
    async def score(self, test_case: TestCase, trace: AgentTrace) -> DimensionScore:
        ...
