# -*- coding: utf-8 -*-
"""把现有 LLM Judge 评估器更新为「含固化参考要点」版本。

两类改动：

1. 多轮评估器（单模 key=Checklist / 对比 key=Criteria）——模板已经引用了要点
   变量，唯一问题是映射目标写死 ``metadata.turn_criteria``，只在多轮逐轮场景
   命中，单轮样例级冻结要点渲染成空串（虽然 ``_append_required_reference_criteria``
   会兜底追加，但模板里留了个空区块 + 底部一段游离要点）。改成
   ``reference_criteria``，由 ``_resolve_source`` 统一解析：多轮取该轮
   turn_criteria，单轮取样例级 reference_criteria。模板一行不动。

2. 非多轮 LLM Judge（正确性/幻觉率/简洁度 × llm-judge/agent + 3 个对比）——
   映射里根本没有要点变量，模板里也没有。补 ``Criteria -> reference_criteria``，
   并在 prompt 末尾追加一行要点。

为什么不走 HTTP：auth 已开启且 token 不便获取。这里在容器内直接用 Repository
复现 ``PUT /api/eval/evaluators/{id}`` 的两步语义（见 api/routers/evaluation.py
的 update_evaluator_instance）：

    a. update_evaluator_config(id, params=new_params)   # 改 config.params
    b. create_evaluator_version(evaluator_id, params)   # 追加快照
       row.current_version_id = version.id              # 指针指向新版

租户处理（关键，容易错）：evaluator_configs 与 evaluator_versions 都继承
TenantMixin。db.py 的 before_flush 只在 tenant_id 为 None 时盖章，显式赋值不动；
do_orm_execute 在 ctx 为 None 时整体旁路过滤。所以本脚本：
  - 以 ctx=None 扫全库（跨租户拿到所有评估器）；
  - 写入时按每个评估器自己的 tenant_id 显式赋给新版本行。
若不显式赋值，新版本行会被盖成 INTERNAL_TENANT_ID，客户租户读不到它，
current_version_id 就成了悬空指针。

只在缺失时改（幂等）；已经是目标形态的跳过。
默认 DRY_RUN，加 --apply 才写库。
"""
from __future__ import annotations

import asyncio
import re
import sys

from agent_eval.db import async_session_factory
from agent_eval.db_models.repository import Repository
from agent_eval.db_models.tables import EvaluatorConfigRow

TARGET_SOURCE = "reference_criteria"
OLD_SOURCE = "metadata.turn_criteria"

# 多轮：只改映射目标，模板不动。key 名各自不同，按存量真名匹配。
MULTITURN_PREFIX = "多轮-"

# 非多轮：要点段按各自模板的语言/变量大小写风格追加。
# 键是评估器 name，值是 (变量名, 追加到 prompt 末尾的行)
NON_MULTITURN_CRITERIA_LINE = {
    "幻觉率/agent": ("Criteria", "Reference criteria (must check one by one): {{Criteria}}"),
    "幻觉率/llm-judge": ("Criteria", "Reference criteria (must check one by one): {{Criteria}}"),
    "正确性/agent": ("Criteria", "Reference criteria (must check one by one): {{Criteria}}"),
    "正确性/llm-judge": ("Criteria", "Reference criteria (must check one by one): {{Criteria}}"),
    "简洁度/agent": ("Criteria", "Reference criteria (must check one by one): {{Criteria}}"),
    "简洁度/llm-judge": ("Criteria", "Reference criteria (must check one by one): {{Criteria}}"),
    "幻觉率对比/llm-judge": ("Criteria", "参考要点（须逐条核对）：{{Criteria}}"),
    "正确性对比/llm-judge": ("Criteria", "参考要点（须逐条核对）：{{Criteria}}"),
    "简洁度对比/llm-judge": ("Criteria", "参考要点（须逐条核对）：{{Criteria}}"),
}

_MUSTACHE_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def plan_multiturn(params: dict) -> dict | None:
    """把映射里所有指向 metadata.turn_criteria 的键改指 reference_criteria。"""
    vm = dict(params.get("variable_mapping") or {})
    hits = [k for k, v in vm.items() if v == OLD_SOURCE]
    if not hits:
        return None
    for k in hits:
        vm[k] = TARGET_SOURCE
    new_params = dict(params)
    new_params["variable_mapping"] = vm
    return new_params


def plan_non_multiturn(name: str, params: dict) -> dict | None:
    """补 Criteria 映射 + 在 prompt 末尾追加要点行。"""
    var_name, line = NON_MULTITURN_CRITERIA_LINE[name]
    vm = dict(params.get("variable_mapping") or {})
    prompt = params.get("evaluation_prompt") or ""

    mapping_ok = vm.get(var_name) == TARGET_SOURCE
    prompt_ok = var_name in set(_MUSTACHE_RE.findall(prompt))
    if mapping_ok and prompt_ok:
        return None

    vm[var_name] = TARGET_SOURCE
    if not prompt_ok:
        prompt = prompt.rstrip("\n") + "\n" + line

    new_params = dict(params)
    new_params["variable_mapping"] = vm
    new_params["evaluation_prompt"] = prompt
    return new_params


def plan_for(name: str, params: dict) -> dict | None:
    if name.startswith(MULTITURN_PREFIX):
        return plan_multiturn(params)
    if name in NON_MULTITURN_CRITERIA_LINE:
        return plan_non_multiturn(name, params)
    return None


async def main() -> int:
    apply = "--apply" in sys.argv

    # 扫描：ctx 保持 None（默认），do_orm_execute 旁路过滤 → 跨租户全量可见。
    async with async_session_factory() as session:
        repo = Repository(session)
        rows = await repo.list_evaluator_configs()

        judges = [r for r in rows if r.evaluator_type == "configurable_judge"]
        planned: list[tuple[EvaluatorConfigRow, dict]] = []
        skipped: list[str] = []

        for row in judges:
            name = row.name or ""
            if not (name.startswith(MULTITURN_PREFIX) or name in NON_MULTITURN_CRITERIA_LINE):
                continue
            new_params = plan_for(name, dict(row.params or {}))
            if new_params is None:
                skipped.append(name)
                continue
            planned.append((row, new_params))

        print(f"TOTAL_JUDGE={len(judges)}")
        print(f"PLANNED={len(planned)}  ALREADY_OK={len(skipped)}")
        for row, np in planned:
            crit_keys = {k: v for k, v in np["variable_mapping"].items() if v == TARGET_SOURCE}
            print(f"  [plan] {row.name}  tenant={row.tenant_id}  criteria_keys={crit_keys}")
        for name in skipped:
            print(f"  [skip] {name} (already target shape)")

        if not apply:
            print("DRY_RUN — pass --apply to write")
            return 0

        # 写入时需要的信息先摘出来，避免跨 session 用已 detach 的 ORM 对象。
        targets = [(r.id, r.name, r.tenant_id, np) for r, np in planned]

    ok = 0
    for eid, name, tenant_id, new_params in targets:
        try:
            async with async_session_factory() as session:
                repo = Repository(session)
                row = await repo.update_evaluator_config(eid, params=new_params)
                if row is None:
                    print(f"  [FAIL] {name} -> config not found")
                    continue
                version = await repo.create_evaluator_version(
                    evaluator_id=row.id,
                    params=new_params,
                    description="patch: 固化参考要点映射到 reference_criteria",
                )
                # 显式带上父配置行的租户，别让 before_flush 盖成内部 sentinel。
                version.tenant_id = tenant_id
                row.current_version_id = version.id
                await session.flush()
                await session.commit()
            ok += 1
            print(f"  [ok] {name} -> v{version.version_number} (tenant={tenant_id})")
        except Exception as e:  # noqa: BLE001 — 单个失败不该中断整批
            print(f"  [FAIL] {name} -> {type(e).__name__}: {e}")

    print(f"APPLIED={ok}/{len(targets)}")
    return 0 if ok == len(targets) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
