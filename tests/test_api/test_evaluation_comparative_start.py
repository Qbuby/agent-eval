"""双模对比评估「启动前」HTTP 层校验回归测试。

盯的是一个真实缺陷：``resolve_eval_start_args`` 调
``_validate_comparative_evaluator_specs`` 时漏了 ``await``、并把 dict 错传给
``repo`` 形参，整段校验成了死代码（漏 await 在 Python 只发 RuntimeWarning，
编译与既有测试都拦不住）。后果是确定性配置错误在 HTTP 层不报，一路漏到
``start_run`` 才抛 ValueError，被包成不透明的「500 failed to start run」。

``test_evaluation/test_comparative.py`` 直接 await runner 内部函数，覆盖不到
HTTP 这一跳——所以缺陷能在全绿测试下进生产。这里一律从 HTTP 解析入口
``resolve_eval_start_args`` 进，断言拒绝是带原因的 400。
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from agent_eval.api.routers.evaluation import resolve_eval_start_args
from agent_eval.api.schemas import EvalAgentConfig, StartEvalRequest

CASE_SOURCE_ID = "11111111-1111-1111-1111-111111111111"
EVALUATOR_ID = "22222222-2222-2222-2222-222222222222"
PROVIDER_ID = "33333333-3333-3333-3333-333333333333"


class _FakeRepo:
    """只实现这条路径会碰到的三个读方法（走上传文件来源，不碰 session）。"""

    def __init__(self, *, evaluator_type: str, params: dict, provider):
        self._evaluator_type = evaluator_type
        self._params = params
        self._provider = provider

    async def get_eval_case_source(self, _source_id):
        return SimpleNamespace(cases=[{"name": "c1", "question": "问题一"}])

    async def get_evaluator_config(self, _config_id):
        return SimpleNamespace(
            id=uuid.UUID(EVALUATOR_ID),
            name="多轮-工具调用正确性对比",
            tag=None,
            evaluator_type=self._evaluator_type,
            params=self._params,
            is_active=True,
            current_version_id=None,
        )

    async def get_evaluator_provider(self, _provider_id):
        return self._provider


def _req() -> StartEvalRequest:
    agent = EvalAgentConfig(type="sse", url="http://a.invalid", model="A")
    return StartEvalRequest(
        case_source_id=CASE_SOURCE_ID,
        agent=agent,
        eval_mode="comparative",
        agent_b=EvalAgentConfig(type="sse", url="http://b.invalid", model="B"),
        evaluator_ids=[EVALUATOR_ID],
    )


_ACTIVE_PROVIDER = SimpleNamespace(is_active=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("evaluator_type", "params", "provider", "message"),
    [
        # 生产上最可能踩的一种：拿单模评估器（非 configurable_judge）去跑对比。
        ("tool_use", {"provider_id": PROVIDER_ID}, _ACTIVE_PROVIDER,
         "must use configurable_judge"),
        ("configurable_judge", {}, _ACTIVE_PROVIDER, "missing provider_id"),
        ("configurable_judge", {"provider_id": PROVIDER_ID}, None,
         "provider not found"),
        ("configurable_judge", {"provider_id": PROVIDER_ID},
         SimpleNamespace(is_active=False), "provider is inactive"),
        # variable_mapping 没接 output_a/output_b：judge 收到的 A/B 恒为空串。
        ("configurable_judge",
         {"provider_id": PROVIDER_ID,
          "variable_mapping": {"answer": "output_text"},
          "evaluation_prompt": "评价 {{answer}}"},
         _ACTIVE_PROVIDER, "output_a / output_b"),
        # mapping 接了，但 prompt 没引用这些占位符，同样收不到 A/B。
        ("configurable_judge",
         {"provider_id": PROVIDER_ID,
          "variable_mapping": {"a": "output_a", "b": "output_b"},
          "evaluation_prompt": "只讲问题 {{question}}"},
         _ACTIVE_PROVIDER, "未引用任何"),
    ],
)
async def test_comparative_config_errors_surface_as_400(
    evaluator_type, params, provider, message,
):
    """确定性配置错误必须在 HTTP 层拒成 400 带原因。

    漏 await / 错传参的旧代码在这里不抛异常（校验成为死代码），本测试即失败。
    """
    repo = _FakeRepo(evaluator_type=evaluator_type, params=params, provider=provider)
    with pytest.raises(HTTPException) as exc_info:
        await resolve_eval_start_args(_req(), session=None, repo=repo)
    assert exc_info.value.status_code == 400
    assert message in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_comparative_valid_config_resolves():
    """正向对照：配置合法时校验放行，并解析出 comparative 的 start_run kwargs。"""
    repo = _FakeRepo(
        evaluator_type="configurable_judge",
        params={"provider_id": PROVIDER_ID},
        provider=_ACTIVE_PROVIDER,
    )
    args = await resolve_eval_start_args(_req(), session=None, repo=repo)
    assert args["eval_mode"] == "comparative"
    assert args["agent_cfg_b"]["model"] == "B"
    assert len(args["cases"]) == 1
