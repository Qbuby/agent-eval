from __future__ import annotations

import time
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from agent_eval.models.trace import AgentTrace, ReasoningStep, ToolCallRecord


class TraceCollectorCallback(BaseCallbackHandler):
    def __init__(self) -> None:
        self.trace = AgentTrace()
        self._llm_start_time: float = 0.0
        self._tool_starts: dict[str, float] = {}

    def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any) -> None:
        self._llm_start_time = time.perf_counter()

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        elapsed = (time.perf_counter() - self._llm_start_time) * 1000

        token_usage: dict[str, int] = {}
        if response.llm_output:
            token_usage = response.llm_output.get("token_usage", {})

        step = ReasoningStep(
            prompt_tokens=token_usage.get("prompt_tokens", 0),
            completion_tokens=token_usage.get("completion_tokens", 0),
            latency_ms=elapsed,
        )

        if response.generations and response.generations[0]:
            generation = response.generations[0][0]
            step.content = generation.text or ""

            if hasattr(generation, "message") and hasattr(generation.message, "tool_calls"):
                step.tool_calls_requested = [
                    {"name": tc["name"], "args": tc.get("args", {})}
                    for tc in (generation.message.tool_calls or [])
                ]

        self.trace.reasoning_steps.append(step)
        self.trace.prompt_tokens += step.prompt_tokens
        self.trace.completion_tokens += step.completion_tokens
        self.trace.total_tokens += step.prompt_tokens + step.completion_tokens

    def on_tool_start(
        self, serialized: dict[str, Any], input_str: str, *, run_id: Any, **kwargs: Any
    ) -> None:
        self._tool_starts[str(run_id)] = time.perf_counter()

    def on_tool_end(self, output: str, *, run_id: Any, **kwargs: Any) -> None:
        run_key = str(run_id)
        start = self._tool_starts.pop(run_key, time.perf_counter())
        record = ToolCallRecord(
            tool_name=kwargs.get("name", serialized.get("name", "unknown") if "serialized" in kwargs else "unknown"),
            tool_input=kwargs.get("inputs", {}),
            tool_output=str(output),
            started_at=start,
            finished_at=time.perf_counter(),
        )
        self.trace.tool_calls.append(record)

    def on_tool_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        run_key = str(run_id)
        start = self._tool_starts.pop(run_key, time.perf_counter())
        record = ToolCallRecord(
            tool_name=kwargs.get("name", "unknown"),
            tool_input=kwargs.get("inputs", {}),
            error=str(error),
            started_at=start,
            finished_at=time.perf_counter(),
        )
        self.trace.tool_calls.append(record)

    def on_agent_finish(self, finish: Any, **kwargs: Any) -> None:
        self.trace.final_output = finish.return_values.get("output", "")
        self.trace.total_latency_ms = sum(s.latency_ms for s in self.trace.reasoning_steps)
