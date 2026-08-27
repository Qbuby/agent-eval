"""带图多轮对话的回归测试（#151）。

多轮样例的 ``input_messages[*].content`` 支持 Anthropic canonical blocks
（``[{"type":"text",...},{"type":"image",...}]``），``replay_conversation``
**原样**把它存进 ``turns[].user``——这样附件能原封不动送进被测 agent。
代价是下游凡是把 ``turns[].user`` 当字符串用的地方，在带图轮上都会拿到 list。

``build_transcript`` 早已用 ``content_to_text`` 处理了这层；但逐轮 judge 的
``input_text`` 入参没有，于是带图轮会一路把 list 传到
``configurable_judge._render``——那里是 ``_MUSTACHE_RE.sub(_sub, template)``，
替换函数返回 list 时 ``re.sub`` 直接抛
``TypeError: expected str instance, list found``。三处受影响：

  * ``multiturn.score_conversation``            （单模逐轮）
  * ``langfuse_runner._run_multiturn_comparative_case``（双模逐轮对比）
  * ``langfuse_runner`` 的补评路径                （rescore 逐轮对比）

约定：**送 judge 的恒为纯文本投影**（附件渲染成 ``[图片]`` 占位），送 agent 的
恒为原始 blocks。judge 是纯文本 LLM，拿不到图，占位符至少让它知道「这一轮用户
附了图」，而不是收到一段 JSON 噪音或直接崩掉。

mock 点沿用 test_multiturn_blank.py 的既有模式：_replay_one_side /
run_comparative_judge / pick_swap（双模）；multiturn.run_configurable_judge（单模）。
"""
from types import SimpleNamespace

import pytest

from agent_eval.api.schemas import EvalResultRow
from agent_eval.data.content_blocks import content_to_text, split_question_content
from agent_eval.db_models.tables import TestResultRow as DbTestResultRow
from agent_eval.evaluation import langfuse_runner, multiturn
from agent_eval.evaluation.configurable_judge import _build_messages
from agent_eval.evaluation.multiturn import build_transcript


# ── 公共构件 ──

def _img_content(text="这张图里的报警码是什么？", url="https://example.com/dash.png"):
    """带图 user content：canonical blocks（文本 + 图片各一块）。"""
    return [
        {"type": "text", "text": text},
        {"type": "image", "source": {"type": "url", "url": url}},
    ]


def _img_turn(idx, assistant, user=None):
    """带图轮：user 是 blocks 数组（回放原样存下的形状）。"""
    return {
        "turn_index": idx, "turn_no": idx,
        "user": user if user is not None else _img_content(),
        "assistant": assistant, "tool_calls": [],
    }


def _text_turn(idx, assistant, user="纯文本问题"):
    return {
        "turn_index": idx, "turn_no": idx,
        "user": user, "assistant": assistant, "tool_calls": [],
    }


def _verdict(sa=0.8, sb=0.6, win="A", overall="A"):
    return {
        "dimensions": [
            {"name": "准确性", "score_a": sa, "score_b": sb, "winner": win, "reason": "r"}
        ],
        "overall_winner": overall,
        "reasoning": "综述",
    }


def _spec():
    return {
        "evaluator_id": "e1",
        "evaluator_version_id": "v1",
        "label": "图片理解",
        "tag": "vision",
        "evaluator_type": "configurable_judge",
        "params": {},
        "_provider": SimpleNamespace(id="p1"),
    }


def _turn_exps(indices):
    return [
        {"turn_index": i, "criteria": ["认出报警码"], "expected_output": "",
         "expected_tool_calls": []}
        for i in indices
    ]


# ── judge 层兜底：blocks 进来也收口成文本 ──

def test_blocks_as_input_text_absorbed_by_resolve_source():
    """钉住兜底层：blocks 数组直接当 input_text 送进 judge 也不炸。

    改造前 ``_render`` 会把 list 交给 ``_MUSTACHE_RE.sub``，直接抛
    ``TypeError: expected str instance, list found``，把整个维度打成 skipped。
    现在 ``_resolve_source`` 统一走 ``content_to_text`` 收口：三处调用点各自
    投影是第一道，这里是第二道——漏一处也只是拿到 ``[图片]`` 占位，不会崩。
    """
    messages = _build_messages(
        params={},
        input_text=_img_content(),  # 故意传 blocks，不预先投影
        output_text="报警码是 E-12",
        expected_output="",
        metadata=None,
    )
    user_prompt = messages[-1]["content"]
    assert isinstance(user_prompt, str)
    assert "这张图里的报警码是什么？" in user_prompt
    assert "[图片]" in user_prompt
    # 兜底结果与调用点自己投影的结果逐字节一致，两道防线语义不分叉。
    assert user_prompt == _build_messages(
        params={},
        input_text=content_to_text(_img_content()),
        output_text="报警码是 E-12",
        expected_output="",
        metadata=None,
    )[-1]["content"]


def test_text_projection_renders_fine():
    """投影成纯文本后，同一模板正常渲染，且 [图片] 占位进得了 prompt。"""
    messages = _build_messages(
        params={},
        input_text=content_to_text(_img_content()),
        output_text="报警码是 E-12",
        expected_output="",
        metadata=None,
    )
    user_prompt = messages[-1]["content"]
    assert "这张图里的报警码是什么？" in user_prompt
    assert "[图片]" in user_prompt


def test_content_to_text_projection_shape():
    """投影结果：文本原样 + 附件占位，恒为 str。"""
    projected = content_to_text(_img_content())
    assert isinstance(projected, str)
    assert projected == "这张图里的报警码是什么？\n[图片]"


def test_result_question_content_snapshot_contract():
    """结果表和 API schema 都保留原始 blocks，供详情页真实渲染附件。"""
    assert "question_content" in DbTestResultRow.__table__.columns
    blocks = _img_content()
    row = EvalResultRow(
        id="r1",
        status="scored",
        question=content_to_text(blocks),
        question_content=blocks,
    )
    assert row.question == "这张图里的报警码是什么？\n[图片]"
    assert row.question_content == blocks


def test_plain_result_question_has_no_content_snapshot():
    """纯文本输入仍只落 question，新增快照列保持 NULL，存量语义不变。"""
    text, blocks = split_question_content("纯文本问题")
    assert text == "纯文本问题"
    assert blocks is None


# ── build_transcript：带图轮（既有行为，防回退） ──

def test_build_transcript_image_turn():
    turns = [_img_turn(0, "报警码是 E-12"), _text_turn(1, "建议检查液压油位")]
    transcript = build_transcript(turns)
    assert "[图片]" in transcript
    assert "这张图里的报警码是什么？" in transcript
    assert "报警码是 E-12" in transcript
    assert "纯文本问题" in transcript


def test_build_transcript_image_only_turn():
    """只有图、没有文字的轮也不能炸（用户只丢一张图上来）。"""
    only_img = [{"type": "image", "source": {"type": "url", "url": "https://x/a.png"}}]
    transcript = build_transcript([_img_turn(0, "看起来是液压报警", user=only_img)])
    assert "[图片]" in transcript
    assert "看起来是液压报警" in transcript


# ── 单模多轮：带图轮送 judge 的是文本投影 ──

@pytest.mark.asyncio
async def test_single_mode_image_turn_judge_gets_text(monkeypatch):
    """单模 score_conversation：带图轮正常出分，judge 的 input_text 是纯文本。"""
    judge_calls = []

    async def fake_judge(*, input_text, output_text, evaluator_name, **_kw):
        judge_calls.append((evaluator_name, input_text, output_text))
        return SimpleNamespace(
            scores=[SimpleNamespace(value=0.8, reason="r", checks=None)],
            error=None,
        )

    monkeypatch.setattr(multiturn, "run_configurable_judge", fake_judge)

    turns = [_img_turn(0, "报警码是 E-12")]
    scores, reasons, checks, failed, last_err = await multiturn.score_conversation(
        turns=turns,
        conversation_goal=None,
        turn_expectations=_turn_exps([0]),
        evaluator_specs=[_spec()],
        case_metadata=None,
        case_id="c1",
    )

    assert failed == 0 and last_err is None
    assert scores["图片理解.turn0"] == 0.8
    assert len(judge_calls) == 1
    _, input_text, _ = judge_calls[0]
    # 关键：judge 拿到的是 str 投影，不是 blocks 数组
    assert isinstance(input_text, str)
    assert "这张图里的报警码是什么？" in input_text
    assert "[图片]" in input_text


@pytest.mark.asyncio
async def test_single_mode_mixed_turns_text_unaffected(monkeypatch):
    """带图轮与纯文本轮混排：纯文本轮的 input_text 逐字节不变（无回归）。"""
    seen = {}

    async def fake_judge(*, input_text, evaluator_name, **_kw):
        seen[evaluator_name] = input_text
        return SimpleNamespace(
            scores=[SimpleNamespace(value=0.7, reason="r", checks=None)],
            error=None,
        )

    monkeypatch.setattr(multiturn, "run_configurable_judge", fake_judge)

    turns = [_img_turn(0, "E-12"), _text_turn(1, "检查液压油位")]
    scores, _, _, failed, _ = await multiturn.score_conversation(
        turns=turns,
        conversation_goal=None,
        turn_expectations=_turn_exps([0, 1]),
        evaluator_specs=[_spec()],
        case_metadata=None,
        case_id="c1",
    )

    assert failed == 0
    assert set(scores) == {"图片理解.turn0", "图片理解.turn1"}
    assert seen["图片理解.turn1"] == "纯文本问题"  # 纯文本轮原样
    assert "[图片]" in seen["图片理解.turn0"]


@pytest.mark.asyncio
async def test_single_mode_image_conversation_level(monkeypatch):
    """会话级打分：transcript 作 output，带图轮渲染成占位，不炸。"""
    calls = []

    async def fake_judge(*, input_text, output_text, evaluator_name, **_kw):
        calls.append((evaluator_name, input_text, output_text))
        return SimpleNamespace(
            scores=[SimpleNamespace(value=0.9, reason="r", checks=None)],
            error=None,
        )

    monkeypatch.setattr(multiturn, "run_configurable_judge", fake_judge)

    scores, _, _, failed, _ = await multiturn.score_conversation(
        turns=[_img_turn(0, "E-12")],
        conversation_goal="识别仪表盘报警并给出处理建议",
        turn_expectations=_turn_exps([0]),
        evaluator_specs=[_spec()],
        case_metadata=None,
        case_id="c1",
    )

    assert failed == 0
    assert scores["图片理解.conversation"] == 0.9
    conv = [c for c in calls if c[0].endswith(".conversation")][0]
    assert isinstance(conv[2], str)
    assert "[图片]" in conv[2]


# ── 双模多轮对比：带图轮送 judge 的是文本投影 ──

async def _run_comparative(monkeypatch, a_turns, b_turns, turn_indices):
    """跑 _run_multiturn_comparative_case，返回 (result, judge_calls)。

    judge_calls 每项是 (input_text, output_a, output_b)。
    """
    async def fake_replay(*, agent_cfg, case_name, **_kw):
        turns = a_turns if case_name.endswith("-A") else b_turns
        return {
            "turns": turns, "tool_calls": [], "steps": [],
            "latency_ms": 1.0, "usage": {}, "attempts": 1, "error": None,
        }

    judge_calls = []

    async def fake_judge(*, input_text, output_a, output_b, **_kw):
        judge_calls.append((input_text, output_a, output_b))
        return SimpleNamespace(verdict=_verdict(), error=None)

    monkeypatch.setattr(langfuse_runner, "_replay_one_side", fake_replay)
    monkeypatch.setattr(langfuse_runner, "run_comparative_judge", fake_judge)
    monkeypatch.setattr(langfuse_runner, "pick_swap", lambda: False)

    result = await langfuse_runner._run_multiturn_comparative_case(
        case={
            "id": "c1", "name": "case", "question": "q",
            "input_messages": [{"role": "user", "content": _img_content()}],
            "turn_expectations": _turn_exps(turn_indices),
            "conversation_goal": None,
        },
        agent_cfg={"name": "A"},
        agent_cfg_b={"name": "B"},
        evaluator_specs=[_spec()],
    )
    return result, judge_calls


def _scoped(result):
    return result["comparison"]["evaluator_verdicts"][0]["scoped_verdicts"]


@pytest.mark.asyncio
async def test_comparative_image_turn_judge_gets_text(monkeypatch):
    """双模带图轮：正常 scored，且对比 judge 的 input_text 是纯文本投影。"""
    a = [_img_turn(0, "报警码 E-12")]
    b = [_img_turn(0, "报警码 E-13")]
    result, judge_calls = await _run_comparative(monkeypatch, a, b, [0])

    by_ti = {s["turn_index"]: s for s in _scoped(result)}
    assert by_ti[0]["status"] == "scored"
    assert len(judge_calls) == 1
    input_text, output_a, output_b = judge_calls[0]
    assert isinstance(input_text, str)
    assert "这张图里的报警码是什么？" in input_text
    assert "[图片]" in input_text
    # 两侧回答原样入槽（未 swap）
    assert output_a == "报警码 E-12" and output_b == "报警码 E-13"


@pytest.mark.asyncio
async def test_comparative_mixed_turns_text_unaffected(monkeypatch):
    """混排：纯文本轮的 input_text 原样，无回归。"""
    a = [_img_turn(0, "E-12"), _text_turn(1, "检查液压油位")]
    b = [_img_turn(0, "E-13"), _text_turn(1, "检查刹车")]
    result, judge_calls = await _run_comparative(monkeypatch, a, b, [0, 1])

    assert all(s["status"] == "scored" for s in _scoped(result))
    assert len(judge_calls) == 2
    inputs = [c[0] for c in judge_calls]
    assert all(isinstance(i, str) for i in inputs)
    assert "[图片]" in inputs[0]
    assert inputs[1] == "纯文本问题"


@pytest.mark.asyncio
async def test_comparative_image_turn_blank_still_skipped(monkeypatch):
    """带图轮上空白跳过逻辑照旧生效（#89 与本次改动不冲突）。"""
    a = [_img_turn(0, "   \n ")]
    b = [_img_turn(0, "报警码 E-13")]
    result, judge_calls = await _run_comparative(monkeypatch, a, b, [0])

    by_ti = {s["turn_index"]: s for s in _scoped(result)}
    assert by_ti[0]["status"] == "skipped"
    assert len(judge_calls) == 0
