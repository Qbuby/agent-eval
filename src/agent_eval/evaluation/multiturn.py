"""多轮对话回放与打分。

Phase 2：把一个多轮对话样例（input_messages 含多条 user/assistant 消息、
可带 conversation_goal 与 turn_expectations）回放给被测 agent，并产出
逐轮分数 + 会话级分数。

设计要点
--------
* **固定 thread_id 逐轮调用**：整段对话共用一个 thread_id（由 runner 传入），
  按 input_messages 里的 user 消息顺序逐轮喂给同一个 adapter 实例。

* **上下文维持按 adapter 类型自动选**（用户决策）：
    - SSE / langgraph_v2（``type in {sse, sse_langgraph}``）：agent 端按
      thread_id 维持上下文，每轮只发**当轮 user 消息**。
    - openai / sse_generic：无服务端会话记忆，客户端自带历史——把累积的
      messages（含之前每轮的 assistant 回复）整段发出。

* **打分复用 configurable_judge**（不改打分内核）：
    - 逐轮：仅对定义了 ``turn_expectations[idx]`` 的轮打分，用该轮 criteria /
      expected_output 作评分依据，score key = ``f"{label}.turn{idx}"``。
    - 会话级：把整段对话拼成 transcript 作 output、conversation_goal 作
      input，调一次 judge，score key = ``f"{label}.conversation"``。
  未定义期望的轮不空跑 judge，避免 N 轮 × M 评估器调用爆炸。

本模块只依赖 runner 传入的 adapter / retry 调用器 / judge 函数，不直接 import
langfuse_runner，避免循环依赖。
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from agent_eval.evaluation.configurable_judge import run_configurable_judge

logger = logging.getLogger(__name__)

# agent 类型 → 是否依赖服务端 thread_id 记忆（每轮只发当轮 user 消息）。
# 不在此集合内的（openai / sse_generic）走客户端自带历史。
_SERVER_MEMORY_TYPES = {"sse", "sse_langgraph"}


def uses_server_memory(agent_type: str | None) -> bool:
    """SSE/langgraph agent 端按 thread_id 记上下文 → True；其余客户端带历史。"""
    return (agent_type or "sse") in _SERVER_MEMORY_TYPES


def _user_turn_indices(messages: list[dict[str, Any]]) -> list[int]:
    """返回 input_messages 里所有 user 消息的下标（turn_expectations.turn_index
    即按此下标对齐）。"""
    return [i for i, m in enumerate(messages) if m.get("role") == "user"]


def build_transcript(turns: list[dict[str, Any]]) -> str:
    """把回放出的逐轮记录拼成可读 transcript，供会话级 judge 当 output。"""
    lines: list[str] = []
    for t in turns:
        u = (t.get("user") or "").strip()
        a = (t.get("assistant") or "").strip()
        if u:
            lines.append(f"用户：{u}")
        if a:
            lines.append(f"助手：{a}")
    return "\n".join(lines)


async def replay_conversation(
    *,
    adapter: Any,
    agent_type: str | None,
    input_messages: list[dict[str, Any]],
    invoke: Callable[[Any, list[dict[str, Any]]], Awaitable[tuple[Any, int]]],
) -> dict[str, Any]:
    """按 user 轮次逐轮回放整段对话，复用同一 adapter（固定 thread_id）。

    ``invoke(adapter, messages) -> (AgentResponse, attempts)`` 由 runner 注入
    （即 ``_invoke_with_retry`` 的偏函数），以复用其重试/取消语义。

    返回：
        {
          "turns": [{turn_index, user, assistant, tool_calls, latency_ms,
                     steps, usage, attempts}, ...],
          "tool_calls": [...合并...],
          "steps": [...合并(带 turn 标记)...],
          "latency_ms": int,        # 各轮之和
          "usage": {prompt_tokens, completion_tokens, total_tokens,
                    cache_creation_tokens, cache_read_tokens},  # 各轮累加
          "attempts": int,          # 各轮最大尝试数
        }
    """
    server_memory = uses_server_memory(agent_type)
    user_idxs = _user_turn_indices(input_messages)

    turns: list[dict[str, Any]] = []
    merged_tool_calls: list[dict[str, Any]] = []
    merged_steps: list[dict[str, Any]] = []
    total_latency = 0.0
    usage_acc = {
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        "cache_creation_tokens": 0, "cache_read_tokens": 0,
    }
    usage_seen = False
    max_attempts = 1

    # 客户端自带历史模式下累积的完整消息序列（含 agent 回复）。
    running: list[dict[str, Any]] = []

    for turn_no, idx in enumerate(user_idxs):
        user_content = input_messages[idx].get("content", "")
        if server_memory:
            # agent 端按 thread_id 记上下文，只发当轮 user 消息。
            send_messages = [{"role": "user", "content": user_content}]
        else:
            # 客户端带历史：把当轮 user 追加到累积序列后整段发出。
            running.append({"role": "user", "content": user_content})
            send_messages = list(running)

        resp, attempts = await invoke(adapter, send_messages)
        max_attempts = max(max_attempts, attempts)
        assistant_text = resp.content or ""

        if not server_memory:
            # 把 agent 回复并入历史，供下一轮上下文。
            running.append({"role": "assistant", "content": assistant_text})

        # 从 raw_response 抽 tool_calls / steps / usage（与单轮同结构）。
        turn_tool_calls: list[dict[str, Any]] = []
        turn_steps: list[dict[str, Any]] = []
        raw = getattr(resp, "raw_response", None)
        if isinstance(raw, dict):
            tcs = raw.get("tool_calls")
            if isinstance(tcs, list):
                turn_tool_calls = tcs
            steps_raw = raw.get("steps")
            if isinstance(steps_raw, list):
                turn_steps = steps_raw
            u = raw.get("usage")
            if isinstance(u, dict):
                inp = u.get("input_tokens") or u.get("prompt_tokens")
                outp = u.get("output_tokens") or u.get("completion_tokens")
                tot = u.get("total_tokens")
                if isinstance(inp, int):
                    usage_acc["prompt_tokens"] += inp
                    usage_seen = True
                if isinstance(outp, int):
                    usage_acc["completion_tokens"] += outp
                    usage_seen = True
                if isinstance(tot, int):
                    usage_acc["total_tokens"] += tot
                    usage_seen = True
                details = u.get("input_token_details") or {}
                if isinstance(details, dict):
                    cc = details.get("cache_creation")
                    cr = details.get("cache_read")
                    if isinstance(cc, int):
                        usage_acc["cache_creation_tokens"] += cc
                    if isinstance(cr, int):
                        usage_acc["cache_read_tokens"] += cr

        total_latency += float(getattr(resp, "latency_ms", 0) or 0)
        merged_tool_calls.extend(turn_tool_calls)
        # steps 打 turn 标记后并入整体 timeline，详情页可按轮分组。
        for s in turn_steps:
            if isinstance(s, dict):
                s = {**s, "turn": turn_no}
            merged_steps.append(s)

        turns.append({
            "turn_index": idx,
            "turn_no": turn_no,
            "user": user_content,
            "assistant": assistant_text,
            "tool_calls": turn_tool_calls,
            "steps": turn_steps,
            "latency_ms": int(getattr(resp, "latency_ms", 0) or 0),
            "attempts": attempts,
        })

    usage = {
        "prompt_tokens": usage_acc["prompt_tokens"] or None,
        "completion_tokens": usage_acc["completion_tokens"] or None,
        "total_tokens": usage_acc["total_tokens"] or None,
        "cache_creation_tokens": usage_acc["cache_creation_tokens"] or None,
        "cache_read_tokens": usage_acc["cache_read_tokens"] or None,
    } if usage_seen else {
        "prompt_tokens": None, "completion_tokens": None, "total_tokens": None,
        "cache_creation_tokens": None, "cache_read_tokens": None,
    }

    return {
        "turns": turns,
        "tool_calls": merged_tool_calls,
        "steps": merged_steps,
        "latency_ms": int(total_latency),
        "usage": usage,
        "attempts": max_attempts,
    }


async def score_conversation(
    *,
    turns: list[dict[str, Any]],
    conversation_goal: str | None,
    turn_expectations: list[dict[str, Any]],
    evaluator_specs: list[dict[str, Any]],
    case_metadata: dict[str, Any] | None,
    case_id: str | None = None,
) -> dict[str, float]:
    """对回放结果做逐轮 + 会话级打分，返回扁平 ``{score_key: value}``。

    只处理 ``configurable_judge`` 评估器（多轮场景下规则类 evaluator 无单一
    expected 概念，本期不接）。score key 约定：
        - 逐轮：``f"{label}.turn{turn_index}"``
        - 会话级：``f"{label}.conversation"``
    """
    scores: dict[str, float] = {}

    # turn_index → 该轮回放记录，便于按期望对齐。
    turn_by_index = {t["turn_index"]: t for t in turns}
    # turn_index → 该轮期望（criteria / expected_output）。
    exp_by_index: dict[int, dict[str, Any]] = {}
    for te in turn_expectations or []:
        ti = te.get("turn_index")
        if isinstance(ti, int):
            exp_by_index[ti] = te

    transcript = build_transcript(turns)

    for spec in evaluator_specs:
        if spec.get("evaluator_type") != "configurable_judge":
            continue
        label = spec.get("label") or "judge"
        provider_row = spec.get("_provider")
        if provider_row is None:
            logger.warning(
                "multiturn score[%s]: skipped (no provider) on case %s", label, case_id
            )
            continue
        params = spec.get("params") or {}

        # ── 逐轮打分：仅对定义了期望的轮 ──
        for ti, te in exp_by_index.items():
            turn = turn_by_index.get(ti)
            if turn is None:
                continue
            criteria = te.get("criteria") or []
            expected = te.get("expected_output") or ""
            # 该轮没有任何评分依据则跳过（不空跑 judge）。
            if not criteria and not expected:
                continue
            # 把该轮 criteria 注入 metadata，judge 模板可用 {{Criteria}} 取。
            turn_meta = dict(case_metadata or {})
            turn_meta["turn_criteria"] = "\n".join(criteria) if criteria else ""
            turn_meta["turn_index"] = ti
            try:
                res = await run_configurable_judge(
                    params=params,
                    provider=provider_row,
                    input_text=turn.get("user", ""),
                    output_text=turn.get("assistant", ""),
                    expected_output=expected,
                    metadata=turn_meta,
                    evaluator_name=f"{label}.turn{ti}",
                )
            except Exception as e:
                logger.warning(
                    "multiturn turn-score[%s.turn%s] crashed on case %s: %s",
                    label, ti, case_id, e,
                )
                continue
            if res.error and not res.scores:
                logger.warning(
                    "multiturn turn-score[%s.turn%s] error on case %s: %s",
                    label, ti, case_id, res.error,
                )
                continue
            for s in res.scores:
                scores[f"{label}.turn{ti}"] = float(s.value)

        # ── 会话级打分：以 conversation_goal 为依据，整段对话作 output ──
        if conversation_goal:
            conv_meta = dict(case_metadata or {})
            conv_meta["conversation_goal"] = conversation_goal
            try:
                res = await run_configurable_judge(
                    params=params,
                    provider=provider_row,
                    input_text=conversation_goal,
                    output_text=transcript,
                    expected_output=conversation_goal,
                    metadata=conv_meta,
                    evaluator_name=f"{label}.conversation",
                )
            except Exception as e:
                logger.warning(
                    "multiturn conv-score[%s] crashed on case %s: %s", label, case_id, e
                )
                continue
            if res.error and not res.scores:
                logger.warning(
                    "multiturn conv-score[%s] error on case %s: %s",
                    label, case_id, res.error,
                )
                continue
            for s in res.scores:
                scores[f"{label}.conversation"] = float(s.value)

    return scores
