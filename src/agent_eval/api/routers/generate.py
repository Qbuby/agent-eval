from __future__ import annotations

import random

from fastapi import APIRouter, Depends, HTTPException

from agent_eval.api.dependencies import (
    get_manager,
    resolve_target_agent_cfg,
)
from agent_eval.api.schemas import GenerateMutateRequest, GenerateScenarioRequest
from agent_eval.auth.dependencies import require_internal
from agent_eval.data.case_generator import CaseGenerator
from agent_eval.data.dataset_manager import DatasetManager
from agent_eval.evaluation.langfuse_runner import _make_adapter

# Generation is a heavy operation restricted to internal roles (admin|user);
# external customers get a clean 403. Router-level dependency also blocks
# anonymous access.
router = APIRouter(
    prefix="/api/generate",
    tags=["generate"],
    dependencies=[Depends(require_internal())],
)


@router.post("/scenario")
async def generate_from_scenario(
    req: GenerateScenarioRequest,
    mgr: DatasetManager = Depends(get_manager),
):
    # The agent-under-test authors cases from its own knowledge graph. The
    # caller may pick one of the pre-configured endpoint presets
    # (agent_endpoint_url); when omitted we fall back to the saved default.
    # We resolve the config + build the adapter here (not via a shared Depends)
    # so the endpoint is per-request, and close it in finally.
    agent_cfg = await resolve_target_agent_cfg(req.agent_endpoint_url)
    adapter = _make_adapter(agent_cfg)
    gen = CaseGenerator(adapter=adapter)
    try:
        # test_scenario is now optional free text. When provided we include it
        # plus the category; when blank we pass only the category so the agent
        # generates freely from its own domain knowledge.
        parts = []
        if req.test_scenario.strip():
            parts.append(f"测试场景/主题: {req.test_scenario.strip()}")
        parts.append(f"样例类别: {req.case_category}")
        scenario = "\n".join(parts)

        tags = [f"category:{req.case_category}"]
        if req.test_scenario.strip():
            tags.append(f"scenario:{req.test_scenario.strip()}")

        # Pull a few existing cases from the dataset as seed examples so the LLM
        # generalizes within the same domain/style instead of inventing random
        # questions. We try a small random sample so consecutive calls don't
        # always anchor on the first 5 rows.
        seed_cases = []
        try:
            all_cases = await mgr.load_cases(req.dataset, limit=200)
            if all_cases:
                sample_n = min(5, len(all_cases))
                seed_cases = random.sample(all_cases, sample_n)
        except Exception:
            # If the dataset doesn't exist yet, generate without seeds rather
            # than blocking the user — the response will still be domain-free
            # but at least it won't error out.
            seed_cases = []

        cases = await gen.generate_from_scenario(
            scenario, count=req.count, context=req.context,
            tags=tags, seed_cases=seed_cases or None,
        )
        if not cases:
            raise HTTPException(status_code=422, detail="agent 未返回有效样例（无法解析出 JSON 数组）")

        # run_agent: replay each question against the agent and store its ACTUAL
        # reply as expected_output (per-turn for multi-turn). Off by default —
        # it roughly doubles generation time (one extra invoke per turn).
        if req.run_agent:
            await gen.fill_expected_from_agent(cases, agent_cfg=agent_cfg)

        result = [c.model_dump(mode="json", exclude_none=True) for c in cases]

        if not req.dry_run:
            await mgr.add_cases_batch(req.dataset, cases)

        return {
            "generated": len(cases),
            "saved": not req.dry_run,
            "cases": result,
            "seed_count": len(seed_cases),
        }
    finally:
        try:
            await adapter.close()
        except Exception:
            pass


@router.post("/mutate")
async def generate_mutations(
    req: GenerateMutateRequest,
    mgr: DatasetManager = Depends(get_manager),
):
    # Same per-request adapter pattern as /scenario: resolve the (optional)
    # endpoint preset, build + close the adapter here.
    agent_cfg = await resolve_target_agent_cfg(req.agent_endpoint_url)
    adapter = _make_adapter(agent_cfg)
    gen = CaseGenerator(adapter=adapter)
    try:
        all_cases = await mgr.load_cases(req.dataset)
        source_case = next((c for c in all_cases if c.id.startswith(req.case_id)), None)
        if not source_case:
            raise HTTPException(status_code=404, detail=f"Case '{req.case_id}' not found")

        cases = await gen.generate_mutations(
            source_case, count=req.count, strategy=req.strategy,
            tags=req.tags or None,
        )
        if not cases:
            raise HTTPException(status_code=422, detail="agent 未返回有效样例（无法解析出 JSON 数组）")

        # run_agent: replay each mutated question against the agent and store its
        # ACTUAL reply as expected_output. Off by default (doubles latency).
        if req.run_agent:
            await gen.fill_expected_from_agent(cases, agent_cfg=agent_cfg)

        result = [c.model_dump(mode="json", exclude_none=True) for c in cases]

        if not req.dry_run:
            target = req.target_dataset or req.dataset
            await mgr.add_cases_batch(target, cases, split=req.split)

        return {"generated": len(cases), "saved": not req.dry_run, "cases": result}
    finally:
        try:
            await adapter.close()
        except Exception:
            pass
