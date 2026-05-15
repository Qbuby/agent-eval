"""Unit tests for Langfuse-backed eval runner.

Focus: pure logic (evaluators, cost aggregation, bench→dataset mapping).
Actual Langfuse roundtrips are covered by the e2e smoke script, not here.
"""
from __future__ import annotations

import pytest

from agent_eval.evaluation.langfuse_runner import (
    _aggregate_cost,
    _bench_case_to_dataset_input,
    _classify_langsmith_error,
    _evaluator_exact_match,
    _evaluator_tool_sequence,
    _extract_tool_calls_from_response,
    _extract_usage,
    _run_matches_question,
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


# ─── _run_matches_question (backfill inner matcher) ─────────────────────────

class _FakeRun:
    def __init__(self, inputs):
        self.inputs = inputs


def test_run_matches_langchain_messages_shape():
    run = _FakeRun({"messages": [{"role": "user", "content": "how do I swap the battery?"}]})
    assert _run_matches_question(run, "how do I swap the battery?") is True
    assert _run_matches_question(run, "different q") is False


def test_run_matches_plain_question_shape():
    run = _FakeRun({"question": "hello"})
    assert _run_matches_question(run, "hello") is True


def test_run_matches_picks_last_user_message():
    run = _FakeRun({"messages": [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "mid"},
        {"role": "user", "content": "second"},
    ]})
    assert _run_matches_question(run, "second") is True
    assert _run_matches_question(run, "first") is False


def test_run_matches_handles_missing_inputs():
    assert _run_matches_question(_FakeRun(None), "x") is False
    assert _run_matches_question(_FakeRun({}), "x") is False
    # messages list exists but last entry isn't a dict
    assert _run_matches_question(_FakeRun({"messages": ["stray"]}), "x") is False


# ─── _classify_langsmith_error (banner category mapping) ─────────────────

def test_classify_403_forbidden():
    e = Exception("Failed to GET /sessions in LangSmith API. HTTPError('403 Client Error: Forbidden for url: ...')")
    assert _classify_langsmith_error(e) == "forbidden"


def test_classify_401_unauthorized():
    assert _classify_langsmith_error(Exception("401 Unauthorized")) == "unauthorized"


def test_classify_404_not_found():
    assert _classify_langsmith_error(Exception("404 Not Found: project missing")) == "not_found"


def test_classify_network_errors():
    assert _classify_langsmith_error(Exception("connection refused")) == "network"
    assert _classify_langsmith_error(Exception("read timed out")) == "network"
    assert _classify_langsmith_error(Exception("DNS lookup failed")) == "network"


def test_classify_unknown_falls_back():
    assert _classify_langsmith_error(Exception("something weird")) == "unknown"


# ─── _classify_agent_error / _is_transient (cold-start guards) ───────────

def test_classify_agent_unreachable_502():
    from agent_eval.evaluation.langfuse_runner import _classify_agent_error
    e = Exception("Server error '502 Bad Gateway' for url 'http://...'")
    assert _classify_agent_error(e) == "agent_unreachable"


def test_classify_agent_unreachable_connection():
    from agent_eval.evaluation.langfuse_runner import _classify_agent_error
    assert _classify_agent_error(Exception("All connection attempts failed")) == "agent_unreachable"
    assert _classify_agent_error(Exception("Connection refused")) == "agent_unreachable"


def test_classify_agent_timeout():
    from agent_eval.evaluation.langfuse_runner import _classify_agent_error
    assert _classify_agent_error(Exception("read timed out")) == "agent_timeout"
    assert _classify_agent_error(Exception("504 Gateway Timeout")) == "agent_timeout"


def test_classify_parse_error():
    from agent_eval.evaluation.langfuse_runner import _classify_agent_error
    assert _classify_agent_error(Exception("JSON decode error at line 1")) == "parse_error"


def test_is_transient_known_signals():
    from agent_eval.evaluation.langfuse_runner import _is_transient
    assert _is_transient(Exception("Connection refused"))
    assert _is_transient(Exception("502 Bad Gateway"))
    assert _is_transient(Exception("timed out"))
    assert not _is_transient(Exception("invalid json"))
