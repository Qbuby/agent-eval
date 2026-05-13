"""Unit tests for Langfuse-backed eval runner.

Focus: pure logic (evaluators, cost aggregation, bench→dataset mapping).
Actual Langfuse roundtrips are covered by the e2e smoke script, not here.
"""
from __future__ import annotations

import pytest

from agent_eval.evaluation.langfuse_runner import (
    _aggregate_cost,
    _bench_case_to_dataset_input,
    _evaluator_exact_match,
    _evaluator_tool_sequence,
    _extract_tool_calls_from_response,
    _extract_usage,
    BUILTIN_EVALUATORS,
)
from agent_eval.evaluation.agent_adapter import AgentResponse


def test_exact_match_case_insensitive_default():
    result = _evaluator_exact_match(
        output="Hello World", expected_output="hello world", params={},
    )
    assert result.scores[0][0] == "exact_match"
    assert result.scores[0][1] == 1.0


def test_exact_match_case_sensitive():
    result = _evaluator_exact_match(
        output="Hello World", expected_output="hello world",
        params={"case_sensitive": True},
    )
    assert result.scores[0][1] == 0.0


def test_exact_match_no_expected():
    result = _evaluator_exact_match(output="x", expected_output="", params={})
    assert result.scores[0][1] == 0.0
    assert "no expected_output" in result.scores[0][2]


def test_tool_sequence_full_match():
    result = _evaluator_tool_sequence(
        expected_tool_calls=[{"tool_name": "a"}, {"tool_name": "b"}],
        actual_tool_calls=[{"tool_name": "a"}, {"tool_name": "b"}],
        params={},
    )
    assert result.scores[0][0] == "tool_sequence_match"
    assert result.scores[0][1] == 1.0


def test_tool_sequence_partial_match():
    result = _evaluator_tool_sequence(
        expected_tool_calls=[{"tool_name": "a"}, {"tool_name": "b"}, {"tool_name": "c"}],
        actual_tool_calls=[{"tool_name": "a"}, {"tool_name": "b"}],
        params={},
    )
    # matched 2, max(3,2) = 3 → 2/3
    assert abs(result.scores[0][1] - 2/3) < 1e-9


def test_tool_sequence_order_mismatch():
    result = _evaluator_tool_sequence(
        expected_tool_calls=[{"tool_name": "a"}, {"tool_name": "b"}],
        actual_tool_calls=[{"tool_name": "b"}, {"tool_name": "a"}],
        params={},
    )
    # prefix match: 0 (both positions mismatch in order)
    assert result.scores[0][1] == 0.0


def test_tool_sequence_passthrough_when_no_expectation():
    result = _evaluator_tool_sequence(
        expected_tool_calls=None, actual_tool_calls=[{"tool_name": "whatever"}],
        params={},
    )
    assert result.scores[0][1] == 1.0
    assert "pass-through" in result.scores[0][2]


def test_aggregate_cost_empty():
    out = _aggregate_cost([])
    assert out == {"count": 0}


def test_aggregate_cost_basic_averages_and_cache():
    rows = [
        {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
         "tool_call_count": 2, "message_count": 3, "latency_ms": 1000,
         "cache_creation_tokens": 20, "cache_read_tokens": 30},
        {"prompt_tokens": 200, "completion_tokens": 80, "total_tokens": 280,
         "tool_call_count": 1, "message_count": 4, "latency_ms": 500,
         "cache_creation_tokens": 40, "cache_read_tokens": 60},
    ]
    out = _aggregate_cost(rows)
    assert out["count"] == 2
    assert out["avg_prompt_tokens"] == 150.0
    assert out["avg_completion_tokens"] == 65.0
    assert out["avg_total_tokens"] == 215.0
    assert out["avg_tool_calls"] == 1.5
    assert out["avg_messages"] == 3.5
    assert out["avg_latency_ms"] == 750.0
    # cache_hit_rate = avg(read) / (avg(prompt) - avg(creation)) = 45 / (150 - 30) = 0.375
    assert abs(out["cache_hit_rate"] - 0.375) < 1e-6


def test_aggregate_cost_skips_none():
    rows = [
        {"prompt_tokens": 100, "completion_tokens": None, "total_tokens": 100,
         "tool_call_count": 0, "message_count": 1, "latency_ms": 500},
        {"prompt_tokens": None, "completion_tokens": 50, "total_tokens": 50,
         "tool_call_count": 1, "message_count": 2, "latency_ms": 200},
    ]
    out = _aggregate_cost(rows)
    assert out["count"] == 2
    assert out["avg_prompt_tokens"] == 100.0  # only 1 non-None sample
    assert out["avg_completion_tokens"] == 50.0
    assert out["cache_hit_rate"] is None  # no cache info


def test_bench_case_to_dataset_input_shape():
    class FakeCase:
        id = "aaaa"
        question = "RPL201 换电池步骤?"
        reference_answer = "答案"
        key_points = ["点1", "点2"]
        tags = ["电池", "维修"]
        difficulty = "medium"
        extra_fields = {"expected_tool_calls": [{"tool_name": "lookup_vehicle"}]}

    out = _bench_case_to_dataset_input(FakeCase())
    assert out["input"]["question"] == "RPL201 换电池步骤?"
    assert out["input"]["messages"][0]["role"] == "user"
    assert out["expected_output"]["answer"] == "答案"
    assert out["expected_output"]["key_points"] == ["点1", "点2"]
    assert out["metadata"]["benchmark_case_id"] == "aaaa"
    assert out["metadata"]["tags"] == ["电池", "维修"]


def test_extract_usage_openai_style():
    resp = AgentResponse(content="x", latency_ms=1.0, raw_response={
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    })
    out = _extract_usage(resp)
    assert out["prompt_tokens"] == 10
    assert out["completion_tokens"] == 5
    assert out["total_tokens"] == 15
    assert out["cache_read_tokens"] is None


def test_extract_usage_anthropic_cache_fields():
    resp = AgentResponse(content="x", latency_ms=1.0, raw_response={
        "usage": {
            "input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
            "input_token_details": {"cache_creation": 20, "cache_read": 30},
        },
    })
    out = _extract_usage(resp)
    assert out["prompt_tokens"] == 100
    assert out["completion_tokens"] == 50
    assert out["total_tokens"] == 150
    assert out["cache_creation_tokens"] == 20
    assert out["cache_read_tokens"] == 30


def test_extract_tool_calls_from_openai_response():
    resp = AgentResponse(content="", latency_ms=1.0, raw_response={
        "choices": [{"message": {
            "content": "",
            "tool_calls": [
                {"id": "t1", "function": {"name": "search", "arguments": "{}"}},
            ],
        }}],
    })
    calls = _extract_tool_calls_from_response(resp)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "search"


def test_extract_tool_calls_no_raw():
    resp = AgentResponse(content="just text", latency_ms=1.0)
    assert _extract_tool_calls_from_response(resp) == []


def test_builtin_evaluators_registry_shape():
    """Every builtin evaluator advertises fn + is_async + description + params_schema."""
    for name, spec in BUILTIN_EVALUATORS.items():
        assert "fn" in spec
        assert "is_async" in spec and isinstance(spec["is_async"], bool)
        assert "description" in spec
        assert "params_schema" in spec and isinstance(spec["params_schema"], dict)
