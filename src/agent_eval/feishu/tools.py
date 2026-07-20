"""内置 agent 的工具集：每个工具 = 一次带 user JWT 的本地 HTTP 调用。

设计要点：
- 工具走 **本地 HTTP 自调**（`http://localhost:8000/api/*`，backend 容器内），
  带该 user 的 Bearer JWT。这样白嫖现有 require_internal/require_admin 门禁 +
  租户 ContextVar 注入 + pydantic 校验——权限边界天然正确，不在机器人进程里
  重复实现门禁（重复 = 越权风险）。删除类端点后端自带 require_admin，非 admin
  用户经机器人触发同样会被 403 挡回，信任边界天然复用。
- 覆盖范围：数据集 / 样例 / 评估器 / provider / 评估运行 的读 + 写全流程。
  写操作（create/update/dry-run/start/stop）直接开放；**删除类不可逆操作**
  （dangerous=True）由上层（orchestrator + bot_service）拦成二次确认，用户回
  「确认」才真正下发。
- 每个工具有 name / description / parameters(JSON schema 子集) / dangerous，
  供编排 LLM 决策；`run(args, token)` 执行并返回结构化结果。

工具清单：
  读——
  list_datasets            GET  /api/datasets
  get_dataset              GET  /api/datasets/{name}
  get_dataset_stats        GET  /api/datasets/{name}/stats
  list_dataset_cases       GET  /api/datasets/{name}/cases
  list_evaluators          GET  /api/eval/evaluators
  list_builtin_evaluators  GET  /api/eval/evaluators/builtin
  list_evaluator_versions  GET  /api/eval/evaluators/{id}/versions
  list_providers           GET  /api/evaluator-providers
  get_provider             GET  /api/evaluator-providers/{id}
  provider_models          GET  /api/evaluator-providers/{id}/models
  list_agent_endpoints     GET  /api/config?category=target_agent
  list_runs                GET  /api/eval/runs
  get_run                  GET  /api/eval/runs/{id}
  get_run_results          GET  /api/eval/runs/{id}/results
  写（开放）——
  create_dataset           POST /api/datasets
  add_case                 POST /api/datasets/{name}/cases
  update_case              PUT  /api/cases/{id}
  create_evaluator         POST /api/eval/evaluators
  update_evaluator         PUT  /api/eval/evaluators/{id}
  dry_run_evaluator        POST /api/eval/evaluators/{id}/dry-run
  create_provider          POST /api/evaluator-providers
  update_provider          PUT  /api/evaluator-providers/{id}
  test_provider            POST /api/evaluator-providers/{id}/test
  start_eval               POST /api/eval/runs/start
  stop_run                 POST /api/eval/runs/{id}/stop
  删除（dangerous，需二次确认）——
  delete_dataset           DELETE /api/datasets/{name}
  delete_case              DELETE /api/cases/{id}
  batch_delete_cases       POST   /api/cases/batch-delete
  delete_evaluator         DELETE /api/eval/evaluators/{id}
  delete_provider          DELETE /api/evaluator-providers/{id}
  delete_run               DELETE /api/eval/runs/{id}
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

# backend 自调基址：容器内 uvicorn 监听 8000。可被 config 覆盖（留常量简单化）。
LOCAL_API_BASE = "http://localhost:8000"


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON-schema 风格的入参说明（供 LLM 决策）
    run: Callable[[dict[str, Any], str], Awaitable[dict[str, Any]]]
    # 危险不可逆操作（删除类）：orchestrator 不直接执行，先让 bot_service 拦成
    # 二次确认，用户明确回「确认」后才由确认通路调用 run。
    dangerous: bool = False


async def _request(
    method: str, path: str, token: str,
    *, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """带 Bearer 的本地 HTTP 调用，收敛成 {ok, status, data|error}。"""
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(
                method, f"{LOCAL_API_BASE}{path}",
                headers=headers, params=params, json=body,
            )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"请求失败：{type(e).__name__}: {e}"}
    return _wrap(resp)


async def _get(path: str, token: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return await _request("GET", path, token, params=params, timeout=30.0)


async def _post(path: str, token: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    return await _request("POST", path, token, body=body or {})


async def _put(path: str, token: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    return await _request("PUT", path, token, body=body or {})


async def _delete(path: str, token: str) -> dict[str, Any]:
    return await _request("DELETE", path, token, timeout=30.0)


def _wrap(resp: httpx.Response) -> dict[str, Any]:
    """把 HTTP 响应收敛成统一结构。4xx/5xx 带上 detail 供 LLM/用户看。"""
    if resp.status_code >= 400:
        detail = ""
        try:
            detail = str(resp.json().get("detail", ""))
        except Exception:  # noqa: BLE001
            detail = resp.text[:200]
        return {"ok": False, "status": resp.status_code, "error": detail or f"HTTP {resp.status_code}"}
    try:
        return {"ok": True, "status": resp.status_code, "data": resp.json()}
    except Exception:  # noqa: BLE001
        return {"ok": True, "status": resp.status_code, "data": resp.text}


def _need(args: dict[str, Any], key: str) -> str | None:
    """取必填字符串参数；缺失返回 None（调用方据此回错）。"""
    v = args.get(key)
    return str(v) if v not in (None, "") else None


# ── 读工具 ──────────────────────────────────────────────────────────────

async def _list_datasets(args: dict[str, Any], token: str) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if args.get("filter"):
        params["filter"] = args["filter"]
    if args.get("type"):
        params["type"] = args["type"]
    return await _get("/api/datasets", token, params or None)


async def _get_dataset(args: dict[str, Any], token: str) -> dict[str, Any]:
    name = _need(args, "name")
    if not name:
        return {"ok": False, "error": "缺少 name（数据集名）"}
    return await _get(f"/api/datasets/{name}", token)


async def _get_dataset_stats(args: dict[str, Any], token: str) -> dict[str, Any]:
    name = _need(args, "name")
    if not name:
        return {"ok": False, "error": "缺少 name（数据集名）"}
    return await _get(f"/api/datasets/{name}/stats", token)


async def _list_dataset_cases(args: dict[str, Any], token: str) -> dict[str, Any]:
    name = _need(args, "name")
    if not name:
        return {"ok": False, "error": "缺少 name（数据集名）"}
    params: dict[str, Any] = {
        "page": args.get("page", 1), "page_size": args.get("page_size", 20),
    }
    for k in ("search", "category", "split"):
        if args.get(k):
            params[k] = args[k]
    return await _get(f"/api/datasets/{name}/cases", token, params)


async def _get_case(args: dict[str, Any], token: str) -> dict[str, Any]:
    """看单条样例的完整详情（问题/参考答案/要点/标签/多轮等）。example_id 必填。"""
    eid = _need(args, "example_id")
    if not eid:
        return {"ok": False, "error": "缺少 example_id（样例 id）"}
    return await _get(f"/api/cases/{eid}", token)


async def _list_evaluators(args: dict[str, Any], token: str) -> dict[str, Any]:
    params = {"active_only": True} if args.get("active_only") else None
    return await _get("/api/eval/evaluators", token, params)


async def _list_builtin_evaluators(args: dict[str, Any], token: str) -> dict[str, Any]:
    return await _get("/api/eval/evaluators/builtin", token)


async def _list_evaluator_versions(args: dict[str, Any], token: str) -> dict[str, Any]:
    eid = _need(args, "evaluator_id")
    if not eid:
        return {"ok": False, "error": "缺少 evaluator_id"}
    return await _get(f"/api/eval/evaluators/{eid}/versions", token)


async def _list_providers(args: dict[str, Any], token: str) -> dict[str, Any]:
    """列出 judge provider（LLM 判分凭证）。api_key 已被后端脱敏（只回掩码/has_api_key）。"""
    return await _get("/api/evaluator-providers", token)


async def _get_provider(args: dict[str, Any], token: str) -> dict[str, Any]:
    pid = _need(args, "provider_id")
    if not pid:
        return {"ok": False, "error": "缺少 provider_id"}
    return await _get(f"/api/evaluator-providers/{pid}", token)


async def _provider_models(args: dict[str, Any], token: str) -> dict[str, Any]:
    pid = _need(args, "provider_id")
    if not pid:
        return {"ok": False, "error": "缺少 provider_id"}
    return await _get(f"/api/evaluator-providers/{pid}/models", token)


async def _list_agent_endpoints(args: dict[str, Any], token: str) -> dict[str, Any]:
    """列出 config 里 target_agent 分类的预设（端点 URL / 超时 / 请求模板 /
    headers 等）。后端对敏感项（api_key）自动过滤，不会返回明文。这是「有哪些
    已配置的 agent 端点可测」的数据源，也是 start_eval 时 agent.url 的来源。"""
    resp = await _get("/api/config", token, {"category": "target_agent"})
    if not resp.get("ok"):
        return resp
    # 只回给 LLM 有用的字段：key + 各 option 的 label/value（default 标注）。
    items = []
    for row in resp.get("data") or []:
        opts = row.get("options") or []
        di = row.get("default_index", 0)
        items.append({
            "key": row.get("key"),
            "description": row.get("description"),
            "options": [
                {"label": o.get("label"), "value": o.get("value"),
                 "is_default": i == di}
                for i, o in enumerate(opts)
            ],
        })
    return {"ok": True, "status": resp.get("status"), "data": items}


async def _list_runs(args: dict[str, Any], token: str) -> dict[str, Any]:
    params: dict[str, Any] = {"page": args.get("page", 1), "page_size": args.get("page_size", 10)}
    for k in ("status", "q"):
        if args.get(k):
            params[k] = args[k]
    return await _get("/api/eval/runs", token, params)


async def _get_run(args: dict[str, Any], token: str) -> dict[str, Any]:
    run_id = _need(args, "run_id")
    if not run_id:
        return {"ok": False, "error": "缺少 run_id"}
    return await _get(f"/api/eval/runs/{run_id}", token)


async def _get_run_results(args: dict[str, Any], token: str) -> dict[str, Any]:
    run_id = _need(args, "run_id")
    if not run_id:
        return {"ok": False, "error": "缺少 run_id"}
    params = {"page": args.get("page", 1), "page_size": args.get("page_size", 20)}
    return await _get(f"/api/eval/runs/{run_id}/results", token, params)


async def _analyze_run(args: dict[str, Any], token: str) -> dict[str, Any]:
    """拉取某次评估运行的 LLM 叙述式分析报告（宏观解读，非逐样例）。"""
    run_id = _need(args, "run_id")
    if not run_id:
        return {"ok": False, "error": "缺少 run_id"}
    # 报告生成含一次 LLM 调用，放宽超时。
    return await _request("GET", f"/api/eval/runs/{run_id}/report", token, timeout=120.0)


# ── 写工具（开放，无需确认）────────────────────────────────────────────

async def _create_dataset(args: dict[str, Any], token: str) -> dict[str, Any]:
    name = _need(args, "name")
    if not name:
        return {"ok": False, "error": "缺少 name（数据集名）"}
    body: dict[str, Any] = {"name": name}
    for k in ("description", "dataset_type", "source_project"):
        if args.get(k) is not None:
            body[k] = args[k]
    return await _post("/api/datasets", token, body)


async def _add_case(args: dict[str, Any], token: str) -> dict[str, Any]:
    """向数据集追加一个样例。case 需含 name + input_messages（非空，每条
    {role, content}，role∈user/assistant/system/tool）。"""
    name = _need(args, "name")
    if not name:
        return {"ok": False, "error": "缺少 name（数据集名）"}
    case = args.get("case")
    if not isinstance(case, dict) or not case.get("input_messages"):
        return {"ok": False, "error": "缺少 case，或 case.input_messages 为空"}
    body: dict[str, Any] = {"cases": [case]}
    if args.get("split"):
        body["split"] = args["split"]
    return await _post(f"/api/datasets/{name}/cases", token, body)


async def _update_case(args: dict[str, Any], token: str) -> dict[str, Any]:
    eid = _need(args, "example_id")
    if not eid:
        return {"ok": False, "error": "缺少 example_id"}
    case = args.get("case")
    if not isinstance(case, dict):
        return {"ok": False, "error": "缺少 case（要更新的样例字段对象）"}
    return await _put(f"/api/cases/{eid}", token, case)


async def _create_evaluator(args: dict[str, Any], token: str) -> dict[str, Any]:
    name = _need(args, "name")
    if not name:
        return {"ok": False, "error": "缺少 name（评估器名）"}
    body: dict[str, Any] = {"name": name}
    for k in ("tag", "evaluator_type", "description", "params", "is_active"):
        if args.get(k) is not None:
            body[k] = args[k]
    return await _post("/api/eval/evaluators", token, body)


async def _update_evaluator(args: dict[str, Any], token: str) -> dict[str, Any]:
    eid = _need(args, "evaluator_id")
    if not eid:
        return {"ok": False, "error": "缺少 evaluator_id"}
    body: dict[str, Any] = {}
    for k in ("name", "tag", "description", "params", "is_active"):
        if args.get(k) is not None:
            body[k] = args[k]
    if not body:
        return {"ok": False, "error": "没有要更新的字段"}
    return await _put(f"/api/eval/evaluators/{eid}", token, body)


async def _dry_run_evaluator(args: dict[str, Any], token: str) -> dict[str, Any]:
    """用一条 (input, output, expected) 试跑评估器打分，不落库、不建新版本。"""
    eid = _need(args, "evaluator_id")
    if not eid:
        return {"ok": False, "error": "缺少 evaluator_id"}
    body: dict[str, Any] = {}
    for k in ("provider_id", "params", "input", "output", "expected_output", "metadata"):
        if args.get(k) is not None:
            body[k] = args[k]
    return await _post(f"/api/eval/evaluators/{eid}/dry-run", token, body)


async def _create_provider(args: dict[str, Any], token: str) -> dict[str, Any]:
    """新建 judge provider。api_key 由用户在对话中提供（会经飞书消息传输），
    落库时后端加密存储、读取时脱敏。"""
    name = _need(args, "name")
    ptype = _need(args, "provider_type")
    if not name or not ptype:
        return {"ok": False, "error": "缺少 name 或 provider_type"}
    body: dict[str, Any] = {"name": name, "provider_type": ptype}
    for k in ("base_url", "api_key", "default_model", "extra_config", "is_active"):
        if args.get(k) is not None:
            body[k] = args[k]
    return await _post("/api/evaluator-providers", token, body)


async def _update_provider(args: dict[str, Any], token: str) -> dict[str, Any]:
    pid = _need(args, "provider_id")
    if not pid:
        return {"ok": False, "error": "缺少 provider_id"}
    body: dict[str, Any] = {}
    for k in ("name", "provider_type", "base_url", "api_key",
              "default_model", "extra_config", "is_active"):
        if args.get(k) is not None:
            body[k] = args[k]
    if not body:
        return {"ok": False, "error": "没有要更新的字段"}
    return await _put(f"/api/evaluator-providers/{pid}", token, body)


async def _test_provider(args: dict[str, Any], token: str) -> dict[str, Any]:
    pid = _need(args, "provider_id")
    if not pid:
        return {"ok": False, "error": "缺少 provider_id"}
    return await _post(f"/api/evaluator-providers/{pid}/test", token, {})


async def _start_eval(args: dict[str, Any], token: str) -> dict[str, Any]:
    """启动评估。args 需含样例来源（四选一）+ agent + evaluator_ids。
    参数由编排 LLM 从对话中抽取，缺失时上层会反问用户。"""
    body = {k: v for k, v in args.items() if v is not None}
    return await _post("/api/eval/runs/start", token, body)


async def _stop_run(args: dict[str, Any], token: str) -> dict[str, Any]:
    run_id = _need(args, "run_id")
    if not run_id:
        return {"ok": False, "error": "缺少 run_id"}
    return await _post(f"/api/eval/runs/{run_id}/stop", token, {})


async def _reaggregate_run(args: dict[str, Any], token: str) -> dict[str, Any]:
    """重算某次运行的聚合分（维度均分等），不重跑评估。适合改了聚合口径后刷新
    历史 run 的汇总。run_id 必填。"""
    run_id = _need(args, "run_id")
    if not run_id:
        return {"ok": False, "error": "缺少 run_id"}
    return await _post(f"/api/eval/runs/{run_id}/reaggregate", token, {})


# ── 飞书多维表格（Bitable）导入 / 导出 ────────────────────────────────────
# 均需用户已完成飞书 OAuth 授权（后端用其 user_access_token 访问私人表）；
# 未授权时后端回 428，工具把该提示如实转回，用户按提示去飞书点授权链接。

async def _import_bitable(args: dict[str, Any], token: str) -> dict[str, Any]:
    """从飞书多维表格批量导入多轮对话样例到指定数据集。

    需 name（目标数据集）+ app_token + table_id；可选 mapping（语义字段→列名，
    覆盖自动识别）、split。数据集需为 conversation 类型（不存在时先 create_dataset）。
    """
    name = _need(args, "name")
    app_token = _need(args, "app_token")
    table_id = _need(args, "table_id")
    if not name or not app_token or not table_id:
        return {"ok": False, "error": "缺少 name（数据集名）/ app_token / table_id"}
    body: dict[str, Any] = {"app_token": app_token, "table_id": table_id}
    if isinstance(args.get("mapping"), dict):
        body["mapping"] = args["mapping"]
    if args.get("split"):
        body["split"] = args["split"]
    return await _request(
        "POST", f"/api/datasets/{name}/cases/import-bitable", token,
        body=body, timeout=120.0,
    )


async def _inspect_bitable(args: dict[str, Any], token: str) -> dict[str, Any]:
    """预览多维表格列头 + 每列样例 + 建议映射（导入前先看结构，供确认 mapping）。"""
    name = _need(args, "name")
    app_token = _need(args, "app_token")
    table_id = _need(args, "table_id")
    if not name or not app_token or not table_id:
        return {"ok": False, "error": "缺少 name（数据集名）/ app_token / table_id"}
    body = {"app_token": app_token, "table_id": table_id}
    return await _request(
        "POST", f"/api/datasets/{name}/cases/import-bitable/inspect", token,
        body=body, timeout=60.0,
    )


async def _export_run_bitable(args: dict[str, Any], token: str) -> dict[str, Any]:
    """把单次评估结果逐样例写入用户指定的飞书多维表格。

    需 run_id + app_token + table_id；include_report=true 时随手在结果里带上
    一段 LLM 分析报告（宏观解读）。写入的是用户自己的私人表（走其 OAuth）。
    """
    run_id = _need(args, "run_id")
    app_token = _need(args, "app_token")
    table_id = _need(args, "table_id")
    if not run_id or not app_token or not table_id:
        return {"ok": False, "error": "缺少 run_id / app_token / table_id"}
    body: dict[str, Any] = {"app_token": app_token, "table_id": table_id}
    if args.get("include_report"):
        body["include_report"] = True
    return await _request(
        "POST", f"/api/eval/runs/{run_id}/export-bitable", token,
        body=body, timeout=180.0,
    )


async def _export_compare_bitable(args: dict[str, Any], token: str) -> dict[str, Any]:
    """把多次评估运行的对比矩阵写入飞书多维表格。

    需 run_ids（非空数组）+ app_token + table_id；可选 align_key（case_id/question）。
    """
    ids = args.get("run_ids")
    app_token = _need(args, "app_token")
    table_id = _need(args, "table_id")
    if not isinstance(ids, list) or not ids:
        return {"ok": False, "error": "缺少 run_ids（非空运行 ID 数组）"}
    if not app_token or not table_id:
        return {"ok": False, "error": "缺少 app_token / table_id"}
    body: dict[str, Any] = {
        "run_ids": ids, "app_token": app_token, "table_id": table_id,
    }
    if args.get("align_key"):
        body["align_key"] = args["align_key"]
    return await _request(
        "POST", "/api/eval/runs/export-compare-bitable", token,
        body=body, timeout=180.0,
    )


# ── 定时评估任务（scheduled_eval_tasks 的 CRUD + 启停 + 立即执行）─────────
# 走本地 HTTP 自调 /api/scheduled-tasks，复用 require_internal 门禁 + 租户注入。
# spec 是一份等价 start_eval 参数的对象（样例来源四选一 + agent + evaluator_ids
# 等）；schedule 形如 {"kind":"interval","seconds":3600} 或 {"kind":"daily","at":"09:00"}。

async def _list_scheduled_tasks(args: dict[str, Any], token: str) -> dict[str, Any]:
    return await _get("/api/scheduled-tasks", token)


async def _get_scheduled_task(args: dict[str, Any], token: str) -> dict[str, Any]:
    tid = _need(args, "task_id")
    if not tid:
        return {"ok": False, "error": "缺少 task_id"}
    return await _get(f"/api/scheduled-tasks/{tid}", token)


async def _create_scheduled_task(args: dict[str, Any], token: str) -> dict[str, Any]:
    """新建定时评估任务。需 name + spec（等价 start_eval 的参数对象）+ schedule。"""
    name = _need(args, "name")
    spec = args.get("spec")
    schedule = args.get("schedule")
    if not name:
        return {"ok": False, "error": "缺少 name（任务名）"}
    if not isinstance(spec, dict) or not spec:
        return {"ok": False, "error": "缺少 spec（等价 start_eval 的参数对象）"}
    if not isinstance(schedule, dict) or not schedule:
        return {"ok": False, "error": '缺少 schedule，如 {"kind":"interval","seconds":3600} 或 {"kind":"daily","at":"09:00"}'}
    body: dict[str, Any] = {"name": name, "spec": spec, "schedule": schedule}
    for k in ("notify_open_ids", "enabled", "created_by"):
        if args.get(k) is not None:
            body[k] = args[k]
    return await _post("/api/scheduled-tasks", token, body)


async def _update_scheduled_task(args: dict[str, Any], token: str) -> dict[str, Any]:
    tid = _need(args, "task_id")
    if not tid:
        return {"ok": False, "error": "缺少 task_id"}
    body: dict[str, Any] = {}
    for k in ("name", "spec", "schedule", "notify_open_ids", "enabled"):
        if args.get(k) is not None:
            body[k] = args[k]
    if not body:
        return {"ok": False, "error": "没有要更新的字段"}
    return await _put(f"/api/scheduled-tasks/{tid}", token, body)


async def _run_scheduled_task_now(args: dict[str, Any], token: str) -> dict[str, Any]:
    tid = _need(args, "task_id")
    if not tid:
        return {"ok": False, "error": "缺少 task_id"}
    return await _post(f"/api/scheduled-tasks/{tid}/run-now", token, {})


async def _pause_scheduled_task(args: dict[str, Any], token: str) -> dict[str, Any]:
    tid = _need(args, "task_id")
    if not tid:
        return {"ok": False, "error": "缺少 task_id"}
    return await _post(f"/api/scheduled-tasks/{tid}/pause", token, {})


async def _resume_scheduled_task(args: dict[str, Any], token: str) -> dict[str, Any]:
    tid = _need(args, "task_id")
    if not tid:
        return {"ok": False, "error": "缺少 task_id"}
    return await _post(f"/api/scheduled-tasks/{tid}/resume", token, {})


async def _delete_scheduled_task(args: dict[str, Any], token: str) -> dict[str, Any]:
    tid = _need(args, "task_id")
    if not tid:
        return {"ok": False, "error": "缺少 task_id"}
    return await _delete(f"/api/scheduled-tasks/{tid}", token)


# ── 项目 / 类别 ─────────────────────────────────────────────────────────

async def _list_projects(args: dict[str, Any], token: str) -> dict[str, Any]:
    """列出所有项目（基准测试集的顶层组织单元）。"""
    return await _get("/api/projects", token)


async def _create_project(args: dict[str, Any], token: str) -> dict[str, Any]:
    name = _need(args, "name")
    if not name:
        return {"ok": False, "error": "缺少 name（项目名）"}
    body: dict[str, Any] = {"name": name}
    if args.get("description") is not None:
        body["description"] = args["description"]
    return await _post("/api/projects", token, body)


async def _list_categories(args: dict[str, Any], token: str) -> dict[str, Any]:
    pid = _need(args, "project_id")
    if not pid:
        return {"ok": False, "error": "缺少 project_id"}
    return await _get(f"/api/projects/{pid}/categories", token)


async def _create_category(args: dict[str, Any], token: str) -> dict[str, Any]:
    pid = _need(args, "project_id")
    name = _need(args, "name")
    if not pid or not name:
        return {"ok": False, "error": "缺少 project_id 或 name（类别名）"}
    body: dict[str, Any] = {"name": name}
    if args.get("description") is not None:
        body["description"] = args["description"]
    return await _post(f"/api/projects/{pid}/categories", token, body)


# ── 样例生成（LLM 造样例 / 变异）─────────────────────────────────────────

async def _generate_cases(args: dict[str, Any], token: str) -> dict[str, Any]:
    """用 LLM 按场景/主题生成测试样例（默认 dry_run 只预览不落库）。
    需 dataset（生成上下文所属数据集名）；可选 test_scenario（主题）、
    case_category（normal/bad_case/edge_case）、count、context、dry_run。"""
    dataset = _need(args, "dataset")
    if not dataset:
        return {"ok": False, "error": "缺少 dataset（数据集名，作生成上下文）"}
    body: dict[str, Any] = {"dataset": dataset}
    for k in ("test_scenario", "case_category", "count", "context", "dry_run"):
        if args.get(k) is not None:
            body[k] = args[k]
    # LLM 生成耗时，放宽超时。
    return await _request("POST", "/api/generate/scenario", token, body=body, timeout=120.0)


async def _mutate_case(args: dict[str, Any], token: str) -> dict[str, Any]:
    """用 LLM 对一条已有样例生成变体。需 dataset + case_id；可选 count、
    strategy（mixed 等）、target_dataset、tags、split、dry_run。"""
    dataset = _need(args, "dataset")
    case_id = _need(args, "case_id")
    if not dataset or not case_id:
        return {"ok": False, "error": "缺少 dataset 或 case_id"}
    body: dict[str, Any] = {"dataset": dataset, "case_id": case_id}
    for k in ("count", "strategy", "target_dataset", "tags", "split", "dry_run"):
        if args.get(k) is not None:
            body[k] = args[k]
    return await _request("POST", "/api/generate/mutate", token, body=body, timeout=120.0)


# ── 候选样例（评审 / 晋级）───────────────────────────────────────────────

async def _list_candidates(args: dict[str, Any], token: str) -> dict[str, Any]:
    """列出候选（暂存区）样例。可按 status（pending/ready/imported/rejected）、
    project_id 过滤，分页。"""
    params: dict[str, Any] = {"page": args.get("page", 1), "page_size": args.get("page_size", 20)}
    for k in ("status", "project_id", "category", "search"):
        if args.get(k):
            params[k] = args[k]
    return await _get("/api/candidates", token, params)


async def _create_candidate(args: dict[str, Any], token: str) -> dict[str, Any]:
    """新建一个候选样例。需 question；可选 answer（有答案则直接 ready）、
    category、project_id、tags 等。"""
    question = _need(args, "question")
    if not question:
        return {"ok": False, "error": "缺少 question（问题文本）"}
    body: dict[str, Any] = {"question": question}
    for k in ("answer", "category", "project_id", "tags", "metadata", "dataset_name"):
        if args.get(k) is not None:
            body[k] = args[k]
    return await _post("/api/candidates", token, body)


async def _review_candidates(args: dict[str, Any], token: str) -> dict[str, Any]:
    """批量评审候选样例。需 ids（非空数组）+ action（approve 或 reject）。"""
    ids = args.get("ids")
    action = _need(args, "action")
    if not isinstance(ids, list) or not ids:
        return {"ok": False, "error": "缺少 ids（非空候选 id 数组）"}
    if action not in ("approve", "reject"):
        return {"ok": False, "error": "action 必须是 approve 或 reject"}
    return await _post("/api/candidates/batch-review", token, {"ids": ids, "action": action})


async def _promote_candidates(args: dict[str, Any], token: str) -> dict[str, Any]:
    """把 ready 的候选样例晋级进正式基准集。需 ids（非空）+ project_id；
    可选 category_id。"""
    ids = args.get("ids")
    pid = _need(args, "project_id")
    if not isinstance(ids, list) or not ids:
        return {"ok": False, "error": "缺少 ids（非空候选 id 数组）"}
    if not pid:
        return {"ok": False, "error": "缺少 project_id（晋级到哪个项目）"}
    body: dict[str, Any] = {"ids": ids, "project_id": pid}
    if args.get("category_id") is not None:
        body["category_id"] = args["category_id"]
    return await _post("/api/candidates/promote", token, body)


# ── 基准样例 / 版本 ─────────────────────────────────────────────────────

async def _list_benchmark_cases(args: dict[str, Any], token: str) -> dict[str, Any]:
    """列出某项目的基准样例。可按 category_id、tag 过滤，分页。"""
    pid = _need(args, "project_id")
    if not pid:
        return {"ok": False, "error": "缺少 project_id"}
    params: dict[str, Any] = {"page": args.get("page", 1), "page_size": args.get("page_size", 20)}
    for k in ("category_id", "tag", "search"):
        if args.get(k):
            params[k] = args[k]
    return await _get(f"/api/benchmark/{pid}/cases", token, params)


async def _create_benchmark_case(args: dict[str, Any], token: str) -> dict[str, Any]:
    """在某项目新建一条基准样例。需 project_id + question；可选
    reference_answer、key_points（数组）、category_id、tags。"""
    pid = _need(args, "project_id")
    question = _need(args, "question")
    if not pid or not question:
        return {"ok": False, "error": "缺少 project_id 或 question"}
    body: dict[str, Any] = {"question": question}
    for k in ("reference_answer", "key_points", "category_id", "tags", "extra_fields"):
        if args.get(k) is not None:
            body[k] = args[k]
    return await _post(f"/api/benchmark/{pid}/cases", token, body)


async def _list_benchmark_versions(args: dict[str, Any], token: str) -> dict[str, Any]:
    """列出某项目的基准版本快照（最新在前）。"""
    pid = _need(args, "project_id")
    if not pid:
        return {"ok": False, "error": "缺少 project_id"}
    return await _get(f"/api/benchmark/{pid}/versions", token)


async def _create_benchmark_version(args: dict[str, Any], token: str) -> dict[str, Any]:
    """给某项目当前的基准样例集打一个版本快照。需 project_id + version_tag；
    可选 description。"""
    pid = _need(args, "project_id")
    tag = _need(args, "version_tag")
    if not pid or not tag:
        return {"ok": False, "error": "缺少 project_id 或 version_tag"}
    body: dict[str, Any] = {"version_tag": tag}
    if args.get("description") is not None:
        body["description"] = args["description"]
    return await _post(f"/api/benchmark/{pid}/versions", token, body)


# ── 只读报表（指标 / 反馈）──────────────────────────────────────────────

async def _metrics_overview(args: dict[str, Any], token: str) -> dict[str, Any]:
    """Langfuse 聚合指标概览（时延 / token / 成本 / 工具成功率 / 错误）。"""
    return await _get("/api/langfuse-metrics/stats", token)


async def _metrics_trends(args: dict[str, Any], token: str) -> dict[str, Any]:
    """指标随时间的趋势（分桶）。"""
    return await _get("/api/langfuse-metrics/trends", token)


async def _feedback_stats(args: dict[str, Any], token: str) -> dict[str, Any]:
    """客户反馈总览（平均分、覆盖率，全局 + 按租户）。"""
    return await _get("/api/feedback/stats", token)


# ── 数据集治理（质量 / 容量 / 去重 / 生命周期）──────────────────────────

async def _dataset_quality(args: dict[str, Any], token: str) -> dict[str, Any]:
    """数据集质量 / 校验报告（缺失字段、异常等）。"""
    name = _need(args, "name")
    if not name:
        return {"ok": False, "error": "缺少 name（数据集名）"}
    return await _get(f"/api/datasets/{name}/quality", token)


async def _dataset_capacity(args: dict[str, Any], token: str) -> dict[str, Any]:
    """数据集容量 / 用量 vs 上限。"""
    name = _need(args, "name")
    if not name:
        return {"ok": False, "error": "缺少 name（数据集名）"}
    return await _get(f"/api/datasets/{name}/capacity", token)


async def _find_duplicates(args: dict[str, Any], token: str) -> dict[str, Any]:
    """查数据集内的重复样例（不改数据，只报告）。"""
    name = _need(args, "name")
    if not name:
        return {"ok": False, "error": "缺少 name（数据集名）"}
    return await _get(f"/api/datasets/{name}/duplicates", token)


# ── 删除工具（dangerous，需二次确认）──────────────────────────────────

async def _delete_dataset(args: dict[str, Any], token: str) -> dict[str, Any]:
    name = _need(args, "name")
    if not name:
        return {"ok": False, "error": "缺少 name（数据集名）"}
    return await _delete(f"/api/datasets/{name}", token)


async def _delete_case(args: dict[str, Any], token: str) -> dict[str, Any]:
    eid = _need(args, "example_id")
    if not eid:
        return {"ok": False, "error": "缺少 example_id"}
    return await _delete(f"/api/cases/{eid}", token)


async def _batch_delete_cases(args: dict[str, Any], token: str) -> dict[str, Any]:
    ids = args.get("example_ids")
    if not isinstance(ids, list) or not ids:
        return {"ok": False, "error": "缺少 example_ids（非空字符串数组）"}
    return await _post("/api/cases/batch-delete", token, {"example_ids": ids})


async def _delete_evaluator(args: dict[str, Any], token: str) -> dict[str, Any]:
    eid = _need(args, "evaluator_id")
    if not eid:
        return {"ok": False, "error": "缺少 evaluator_id"}
    return await _delete(f"/api/eval/evaluators/{eid}", token)


async def _delete_provider(args: dict[str, Any], token: str) -> dict[str, Any]:
    pid = _need(args, "provider_id")
    if not pid:
        return {"ok": False, "error": "缺少 provider_id"}
    return await _delete(f"/api/evaluator-providers/{pid}", token)


async def _delete_run(args: dict[str, Any], token: str) -> dict[str, Any]:
    run_id = _need(args, "run_id")
    if not run_id:
        return {"ok": False, "error": "缺少 run_id"}
    return await _delete(f"/api/eval/runs/{run_id}", token)


async def _delete_category(args: dict[str, Any], token: str) -> dict[str, Any]:
    """删除项目类别（类别下有样例时后端拒绝）。要求 admin。"""
    cid = _need(args, "category_id")
    if not cid:
        return {"ok": False, "error": "缺少 category_id"}
    return await _delete(f"/api/projects/categories/{cid}", token)


async def _archive_dataset(args: dict[str, Any], token: str) -> dict[str, Any]:
    """归档数据集（禁新导入，历史保留）。后端为 POST。"""
    name = _need(args, "name")
    if not name:
        return {"ok": False, "error": "缺少 name（数据集名）"}
    return await _post(f"/api/datasets/{name}/archive", token)


async def _activate_dataset(args: dict[str, Any], token: str) -> dict[str, Any]:
    """重新激活已归档的数据集。后端为 POST。"""
    name = _need(args, "name")
    if not name:
        return {"ok": False, "error": "缺少 name（数据集名）"}
    return await _post(f"/api/datasets/{name}/activate", token)


async def _dedupe_dataset(args: dict[str, Any], token: str) -> dict[str, Any]:
    """对数据集去重。strategy：skip/replace/append_suffix，缺省交后端默认。"""
    name = _need(args, "name")
    if not name:
        return {"ok": False, "error": "缺少 name（数据集名）"}
    body: dict[str, Any] = {}
    strategy = _need(args, "strategy")
    if strategy:
        body["strategy"] = strategy
    return await _post(f"/api/datasets/{name}/deduplicate", token, body or None)


# ── 注册表 ──────────────────────────────────────────────────────────────

TOOLS: dict[str, Tool] = {
    # ---- 读 ----
    "list_datasets": Tool(
        name="list_datasets",
        description="列出数据集（可选 filter 名称模糊过滤、type 按 candidate/conversation 过滤）。",
        parameters={"filter": "可选，按名称模糊过滤", "type": "可选，candidate 或 conversation"},
        run=_list_datasets,
    ),
    "get_dataset": Tool(
        name="get_dataset",
        description="查看单个数据集详情（描述、样例数、类型、来源项目）。",
        parameters={"name": "必填，数据集名"},
        run=_get_dataset,
    ),
    "get_dataset_stats": Tool(
        name="get_dataset_stats",
        description="查看数据集统计（总样例数、按来源/标签分布、期望输出/criteria 覆盖等）。",
        parameters={"name": "必填，数据集名"},
        run=_get_dataset_stats,
    ),
    "list_dataset_cases": Tool(
        name="list_dataset_cases",
        description="分页列出数据集内的样例。可 search 模糊搜、category 精确过滤、split 过滤。",
        parameters={
            "name": "必填，数据集名", "search": "可选，name/description 模糊搜",
            "category": "可选，受管类别名", "split": "可选，split 名",
            "page": "可选，页码，默认1", "page_size": "可选，每页数，默认20（≤100）",
        },
        run=_list_dataset_cases,
    ),
    "get_case": Tool(
        name="get_case",
        description="查看单个样例的完整详情（问题/参考答案/关键点/标签等）。用户问「看看第 N 条/某条样例的完整内容」时用它。",
        parameters={"example_id": "必填，样例 id"},
        run=_get_case,
    ),
    "list_evaluators": Tool(
        name="list_evaluators",
        description="列出评估器实例（active_only=true 只看启用的）。",
        parameters={"active_only": "可选，布尔"},
        run=_list_evaluators,
    ),
    "list_builtin_evaluators": Tool(
        name="list_builtin_evaluators",
        description="列出内置评估器模板及其参数 schema。",
        parameters={},
        run=_list_builtin_evaluators,
    ),
    "list_evaluator_versions": Tool(
        name="list_evaluator_versions",
        description="列出某评估器的历史版本（configurable_judge 的 params 快照，最新在前）。",
        parameters={"evaluator_id": "必填，评估器 id"},
        run=_list_evaluator_versions,
    ),
    "list_providers": Tool(
        name="list_providers",
        description="列出 judge provider（LLM 判分凭证）。api_key 已脱敏，只返回掩码。",
        parameters={},
        run=_list_providers,
    ),
    "get_provider": Tool(
        name="get_provider",
        description="查看单个 judge provider 详情（api_key 脱敏）。",
        parameters={"provider_id": "必填，provider id"},
        run=_get_provider,
    ),
    "provider_models": Tool(
        name="provider_models",
        description="拉取某 provider 可用模型列表（用于挑选 default_model）。",
        parameters={"provider_id": "必填，provider id"},
        run=_provider_models,
    ),
    "list_agent_endpoints": Tool(
        name="list_agent_endpoints",
        description=(
            "列出已配置的被测 agent 端点预设（target_agent 分类：端点 URL、超时、"
            "请求模板、headers 等）。回答『有哪些已配置的 agent 端点可以测』就用它；"
            "start_eval 时 agent.url 也可引用这里的预设值。api_key 已被后端隐藏。"
        ),
        parameters={},
        run=_list_agent_endpoints,
    ),
    "list_runs": Tool(
        name="list_runs",
        description="列出评估运行历史。可按 status 过滤、q 文本搜索、分页。",
        parameters={
            "status": "可选，运行状态", "q": "可选，文本搜索",
            "page": "可选，页码，默认1", "page_size": "可选，每页数，默认10",
        },
        run=_list_runs,
    ),
    "get_run": Tool(
        name="get_run",
        description="查看某次评估运行的详情（含维度平均分、状态）。",
        parameters={"run_id": "必填，运行 ID"},
        run=_get_run,
    ),
    "get_run_results": Tool(
        name="get_run_results",
        description="查看某次评估运行的逐样例结果（分页）。",
        parameters={
            "run_id": "必填，运行 ID",
            "page": "可选，页码", "page_size": "可选，每页数，默认20",
        },
        run=_get_run_results,
    ),
    "analyze_run": Tool(
        name="analyze_run",
        description=(
            "生成某次评估运行的 LLM 叙述式分析报告（总体结论 / 维度表现 / 工具使用 / "
            "成本效率 / 改进建议）。用户问『帮我分析下这次评估』『这次跑得怎么样』时用它。"
            "返回一段 markdown 文本，直接转述给用户即可。"
        ),
        parameters={"run_id": "必填，运行 ID"},
        run=_analyze_run,
    ),
    # ---- 写（开放）----
    "create_dataset": Tool(
        name="create_dataset",
        description="新建数据集。dataset_type：candidate（默认，单轮备选）或 conversation（多轮对话）。",
        parameters={
            "name": "必填，数据集名", "description": "可选，描述",
            "dataset_type": "可选，candidate 或 conversation，默认 candidate",
            "source_project": "可选，来源项目名",
        },
        run=_create_dataset,
    ),
    "add_case": Tool(
        name="add_case",
        description=(
            "向数据集追加一个样例。case 是对象，必含 name 和 input_messages"
            "（非空数组，每条 {role, content}，role∈user/assistant/system/tool）。"
            "可选：description、tags、category、expected_output、"
            "expected_output_criteria、conversation_goal、turn_expectations 等。"
        ),
        parameters={
            "name": "必填，数据集名",
            "case": "必填，样例对象 {name, input_messages:[{role,content}], ...}",
            "split": "可选，split 名",
        },
        run=_add_case,
    ),
    "update_case": Tool(
        name="update_case",
        description="更新一个样例。case 是完整样例字段对象（同 add_case 的 case 结构）。",
        parameters={
            "example_id": "必填，样例 id",
            "case": "必填，样例对象 {name, input_messages:[{role,content}], ...}",
        },
        run=_update_case,
    ),
    "create_evaluator": Tool(
        name="create_evaluator",
        description=(
            "新建评估器实例。configurable_judge 类型需在 params 里带 provider_id + "
            "prompt/dimensions 等配置；tag 缺省用 name。"
        ),
        parameters={
            "name": "必填，评估器名", "tag": "可选，标签，缺省=name",
            "evaluator_type": "可选，如 configurable_judge",
            "description": "可选，描述", "params": "可选，配置对象",
            "is_active": "可选，布尔，默认 true",
        },
        run=_create_evaluator,
    ),
    "update_evaluator": Tool(
        name="update_evaluator",
        description="更新评估器。改 params（configurable_judge）会自动追加新版本并激活。",
        parameters={
            "evaluator_id": "必填，评估器 id",
            "name": "可选", "tag": "可选", "description": "可选",
            "params": "可选，配置对象", "is_active": "可选，布尔",
        },
        run=_update_evaluator,
    ),
    "dry_run_evaluator": Tool(
        name="dry_run_evaluator",
        description=(
            "用一条样例试跑评估器打分（不落库）。需 provider_id（或 params 里带）+ "
            "input/output，可选 expected_output。用于调 prompt 时快速验证。"
        ),
        parameters={
            "evaluator_id": "必填，评估器 id",
            "provider_id": "可选，覆盖 provider（不传则用 params 里的）",
            "params": "可选，草稿配置（覆盖已存的）",
            "input": "输入文本", "output": "被测输出文本",
            "expected_output": "可选，期望输出", "metadata": "可选，附加元数据对象",
        },
        run=_dry_run_evaluator,
    ),
    "create_provider": Tool(
        name="create_provider",
        description=(
            "新建 judge provider（LLM 判分凭证）。provider_type 如 openai / "
            "openai_compatible / anthropic / deepseek / azure。api_key 会经消息传输、"
            "后端加密存储。"
        ),
        parameters={
            "name": "必填，provider 名", "provider_type": "必填，类型",
            "base_url": "可选，自定义 base_url", "api_key": "可选，密钥",
            "default_model": "可选，默认模型", "extra_config": "可选，附加配置对象",
            "is_active": "可选，布尔，默认 true",
        },
        run=_create_provider,
    ),
    "update_provider": Tool(
        name="update_provider",
        description="更新 judge provider。api_key 传空串=清除、传值=替换、不传=保持不变。",
        parameters={
            "provider_id": "必填，provider id",
            "name": "可选", "provider_type": "可选", "base_url": "可选",
            "api_key": "可选", "default_model": "可选",
            "extra_config": "可选，对象", "is_active": "可选，布尔",
        },
        run=_update_provider,
    ),
    "test_provider": Tool(
        name="test_provider",
        description="连通性测试某 provider（发一次探针请求），返回是否可用 + 延迟 + 模型样本。",
        parameters={"provider_id": "必填，provider id"},
        run=_test_provider,
    ),
    "start_eval": Tool(
        name="start_eval",
        description=(
            "启动一次评估。必填：样例来源（benchmark_version_id / project_id / "
            "case_source_id / conversation_dataset 四选一）、agent（被测智能体配置，"
            "含 type/url）、evaluator_ids（评估器 id 列表，非空）。可选：concurrency、"
            "run_name、limit、filter_category_id、case_ids。参数不全时先反问用户，"
            "不要臆造 URL 或 id。"
        ),
        parameters={
            "benchmark_version_id": "样例来源之一", "project_id": "样例来源之一",
            "case_source_id": "样例来源之一", "conversation_dataset": "样例来源之一（多轮对话集名）",
            "agent": "必填，对象 {type, url, api_key?, model?, timeout?}",
            "evaluator_ids": "必填，字符串数组，非空",
            "concurrency": "可选，1..20，默认3", "run_name": "可选",
            "limit": "可选，最多跑多少条", "filter_category_id": "可选，按分类",
            "case_ids": "可选，手动勾选的样例 id 数组",
        },
        run=_start_eval,
    ),
    "stop_run": Tool(
        name="stop_run",
        description="请求停止一次正在运行的评估（置为 stopping）。",
        parameters={"run_id": "必填，运行 ID"},
        run=_stop_run,
    ),
    "reaggregate_run": Tool(
        name="reaggregate_run",
        description="重算某次评估运行的聚合分（维度均分等），不重跑样例。改了评估器权重/口径后想刷新汇总时用它。",
        parameters={"run_id": "必填，运行 ID"},
        run=_reaggregate_run,
    ),
    # ---- 飞书多维表格导入/导出（需用户已完成飞书 OAuth 授权）----
    "inspect_bitable": Tool(
        name="inspect_bitable",
        description=(
            "导入前预览飞书多维表格结构：返回列头、每列样例值、建议的语义字段→"
            "列名映射。用于确认 mapping 再导入。需用户已完成飞书授权。"
        ),
        parameters={
            "name": "必填，目标数据集名", "app_token": "必填，多维表格 app_token",
            "table_id": "必填，数据表 id",
        },
        run=_inspect_bitable,
    ),
    "import_bitable": Tool(
        name="import_bitable",
        description=(
            "从飞书多维表格批量导入多轮对话样例到数据集。需 name（conversation "
            "类型数据集，不存在先 create_dataset）+ app_token + table_id；可选 "
            "mapping（语义字段→列名，覆盖自动识别）、split。建议先 inspect_bitable "
            "看结构。需用户已完成飞书授权。"
        ),
        parameters={
            "name": "必填，目标数据集名", "app_token": "必填，多维表格 app_token",
            "table_id": "必填，数据表 id",
            "mapping": "可选，语义字段→列名映射对象", "split": "可选，split 名",
        },
        run=_import_bitable,
    ),
    "export_run_bitable": Tool(
        name="export_run_bitable",
        description=(
            "把单次评估结果逐样例导出到飞书多维表格。需 run_id + app_token + "
            "table_id；include_report=true 时附带一段 LLM 分析报告。写入用户自己的"
            "私人表。需用户已完成飞书授权。"
        ),
        parameters={
            "run_id": "必填，运行 ID", "app_token": "必填，多维表格 app_token",
            "table_id": "必填，数据表 id",
            "include_report": "可选，布尔，是否附带 LLM 分析报告",
        },
        run=_export_run_bitable,
    ),
    "export_compare_bitable": Tool(
        name="export_compare_bitable",
        description=(
            "把多次评估运行的对比矩阵导出到飞书多维表格。需 run_ids（非空数组）+ "
            "app_token + table_id；可选 align_key（case_id/question，默认 case_id）。"
            "需用户已完成飞书授权。"
        ),
        parameters={
            "run_ids": "必填，运行 ID 数组，非空", "app_token": "必填，多维表格 app_token",
            "table_id": "必填，数据表 id",
            "align_key": "可选，对齐键 case_id 或 question，默认 case_id",
        },
        run=_export_compare_bitable,
    ),
    # ---- 定时评估任务（管理面板 + 立即执行）----
    "list_scheduled_tasks": Tool(
        name="list_scheduled_tasks",
        description="列出当前租户的定时评估任务（名称、调度、启用状态、下次/上次运行）。",
        parameters={},
        run=_list_scheduled_tasks,
    ),
    "get_scheduled_task": Tool(
        name="get_scheduled_task",
        description="查看单个定时评估任务详情（含完整 spec 与 schedule）。",
        parameters={"task_id": "必填，定时任务 id"},
        run=_get_scheduled_task,
    ),
    "create_scheduled_task": Tool(
        name="create_scheduled_task",
        description=(
            "新建定时评估任务。需 name + spec（等价 start_eval 的参数对象：样例来源 + "
            "agent + evaluator_ids 等）+ schedule。schedule 形如 "
            '{"kind":"interval","seconds":3600} 或 {"kind":"daily","at":"09:00"}（at 按 UTC）。'
            "可选 notify_open_ids（完成后额外通知的 open_id 列表）、enabled（默认 true）。"
            "参数不全时先反问用户，不要臆造样例来源或 agent。"
        ),
        parameters={
            "name": "必填，任务名",
            "spec": "必填，评估参数对象（同 start_eval：样例来源/agent/evaluator_ids/...）",
            "schedule": "必填，调度对象 {kind:interval,seconds} 或 {kind:daily,at:HH:MM}",
            "notify_open_ids": "可选，完成后额外通知的 open_id 数组",
            "enabled": "可选，布尔，默认 true",
        },
        run=_create_scheduled_task,
    ),
    "update_scheduled_task": Tool(
        name="update_scheduled_task",
        description=(
            "更新定时评估任务。可改 name / spec / schedule / notify_open_ids / enabled，"
            "只传要改的字段。改 schedule 或重新启用会重算下次运行时刻。"
        ),
        parameters={
            "task_id": "必填，定时任务 id",
            "name": "可选", "spec": "可选，评估参数对象",
            "schedule": "可选，调度对象", "notify_open_ids": "可选，open_id 数组",
            "enabled": "可选，布尔",
        },
        run=_update_scheduled_task,
    ),
    "run_scheduled_task_now": Tool(
        name="run_scheduled_task_now",
        description="立即执行一次该定时任务的评估（不影响其定时节奏），返回 run_id。",
        parameters={"task_id": "必填，定时任务 id"},
        run=_run_scheduled_task_now,
    ),
    "pause_scheduled_task": Tool(
        name="pause_scheduled_task",
        description="暂停定时任务（不再自动触发，直到 resume）。",
        parameters={"task_id": "必填，定时任务 id"},
        run=_pause_scheduled_task,
    ),
    "resume_scheduled_task": Tool(
        name="resume_scheduled_task",
        description="恢复被暂停的定时任务，按其 schedule 重算下次运行时刻。",
        parameters={"task_id": "必填，定时任务 id"},
        run=_resume_scheduled_task,
    ),
    # ---- 项目 / 类别 ----
    "list_projects": Tool(
        name="list_projects",
        description="列出所有项目（评估的顶层组织单元，基准样例挂在项目下）。",
        parameters={},
        run=_list_projects,
    ),
    "create_project": Tool(
        name="create_project",
        description="新建项目。name 需唯一。",
        parameters={"name": "必填，项目名", "description": "可选，描述"},
        run=_create_project,
    ),
    "list_categories": Tool(
        name="list_categories",
        description="列出某项目下的受管类别。",
        parameters={"project_id": "必填，项目 id"},
        run=_list_categories,
    ),
    "create_category": Tool(
        name="create_category",
        description="在项目下新建类别（已存在同名则返回现有）。",
        parameters={
            "project_id": "必填，项目 id", "name": "必填，类别名",
            "description": "可选，描述",
        },
        run=_create_category,
    ),
    # ---- 样例生成（LLM，耗时较长）----
    "generate_cases": Tool(
        name="generate_cases",
        description=(
            "用 LLM 围绕场景/主题生成测试样例。需 dataset（生成所依据的数据集名，"
            "取其领域上下文）；可选 test_scenario（主题，留空则自由出题）、"
            "case_category（normal/bad_case/edge_case，默认 normal）、count（默认5）、"
            "dry_run（默认 true 只预览不落库；false 才写入 dataset）。"
        ),
        parameters={
            "dataset": "必填，依据的数据集名", "test_scenario": "可选，主题/场景自由文本",
            "case_category": "可选，normal/bad_case/edge_case，默认 normal",
            "count": "可选，生成条数，默认5", "context": "可选，附加上下文",
            "dry_run": "可选，布尔，默认 true（只预览）",
        },
        run=_generate_cases,
    ),
    "mutate_case": Tool(
        name="mutate_case",
        description=(
            "用 LLM 对一条已有样例生成变体。需 dataset + case_id；可选 count（默认3）、"
            "strategy（mixed/…，默认 mixed）、target_dataset（变体写入的目标集，默认同 dataset）、"
            "dry_run（默认 false 会落库）。"
        ),
        parameters={
            "dataset": "必填，源样例所在数据集名", "case_id": "必填，源样例 id",
            "count": "可选，变体数，默认3", "strategy": "可选，变异策略，默认 mixed",
            "target_dataset": "可选，变体写入目标集", "dry_run": "可选，布尔，默认 false",
        },
        run=_mutate_case,
    ),
    # ---- 候选样例（评审 / 入库流）----
    "list_candidates": Tool(
        name="list_candidates",
        description="列出候选（暂存）样例。可按 status（pending/ready/imported/rejected）、project_id 过滤，分页。",
        parameters={
            "status": "可选，pending/ready/imported/rejected",
            "project_id": "可选，项目 id",
            "page": "可选，页码，默认1", "page_size": "可选，每页数，默认20",
        },
        run=_list_candidates,
    ),
    "create_candidate": Tool(
        name="create_candidate",
        description="新建候选样例。有 answer 则状态置 ready，否则 pending。",
        parameters={
            "question": "必填，问题", "answer": "可选，参考答案",
            "project_id": "可选，项目 id", "category": "可选，类别名",
        },
        run=_create_candidate,
    ),
    "review_candidates": Tool(
        name="review_candidates",
        description="批量评审候选样例：approve（通过→ready）或 reject（拒绝）。",
        parameters={
            "ids": "必填，候选样例 id 数组，非空",
            "action": "必填，approve 或 reject",
        },
        run=_review_candidates,
    ),
    "promote_candidates": Tool(
        name="promote_candidates",
        description="把 ready 状态的候选样例晋升进基准测试集。需 ids + project_id；可选 category_id。",
        parameters={
            "ids": "必填，候选样例 id 数组，非空", "project_id": "必填，目标项目 id",
            "category_id": "可选，目标类别 id",
        },
        run=_promote_candidates,
    ),
    # ---- 基准样例 / 版本 ----
    "list_benchmark_cases": Tool(
        name="list_benchmark_cases",
        description="列出某项目的基准样例。可按 category_id、tag 过滤，分页。",
        parameters={
            "project_id": "必填，项目 id", "category_id": "可选，类别 id",
            "tag": "可选，标签", "page": "可选，页码", "page_size": "可选，每页数",
        },
        run=_list_benchmark_cases,
    ),
    "create_benchmark_case": Tool(
        name="create_benchmark_case",
        description="在项目下新建一条基准样例。需 project_id + question；可选 reference_answer、category_id、key_points。",
        parameters={
            "project_id": "必填，项目 id", "question": "必填，问题",
            "reference_answer": "可选，参考答案", "category_id": "可选，类别 id",
            "key_points": "可选，关键点字符串数组",
        },
        run=_create_benchmark_case,
    ),
    "list_benchmark_versions": Tool(
        name="list_benchmark_versions",
        description="列出某项目的基准版本快照。",
        parameters={"project_id": "必填，项目 id"},
        run=_list_benchmark_versions,
    ),
    "create_benchmark_version": Tool(
        name="create_benchmark_version",
        description="为项目当前基准样例打一个版本快照。需 project_id + version_tag；可选 description。",
        parameters={
            "project_id": "必填，项目 id", "version_tag": "必填，版本标签",
            "description": "可选，描述",
        },
        run=_create_benchmark_version,
    ),
    # ---- 只读报表 ----
    "metrics_overview": Tool(
        name="metrics_overview",
        description="Langfuse 聚合指标总览（时延/token/成本/工具成功率/错误）。可选 days 回看天数。",
        parameters={"days": "可选，回看天数"},
        run=_metrics_overview,
    ),
    "metrics_trends": Tool(
        name="metrics_trends",
        description="Langfuse 指标随时间的分桶趋势。可选 days 回看天数。",
        parameters={"days": "可选，回看天数"},
        run=_metrics_trends,
    ),
    "feedback_stats": Tool(
        name="feedback_stats",
        description="客户反馈总览（全局 + 各租户平均分、覆盖率）。",
        parameters={},
        run=_feedback_stats,
    ),
    # ---- 数据集治理（只读诊断）----
    "dataset_quality": Tool(
        name="dataset_quality",
        description="数据集质量/校验报告（缺失字段、异常等）。",
        parameters={"name": "必填，数据集名"},
        run=_dataset_quality,
    ),
    "dataset_capacity": Tool(
        name="dataset_capacity",
        description="数据集容量/用量对比上限。",
        parameters={"name": "必填，数据集名"},
        run=_dataset_capacity,
    ),
    "find_duplicates": Tool(
        name="find_duplicates",
        description="查找数据集内的重复样例（只读，不删）。",
        parameters={"name": "必填，数据集名"},
        run=_find_duplicates,
    ),
    # ---- 删除（dangerous，需二次确认）----
    "delete_dataset": Tool(
        name="delete_dataset",
        description="删除数据集（软删，之后列表不再可见）。不可逆，需二次确认，且要求 admin 角色。",
        parameters={"name": "必填，数据集名"},
        run=_delete_dataset,
        dangerous=True,
    ),
    "delete_case": Tool(
        name="delete_case",
        description="删除单个样例。不可逆，需二次确认，且要求 admin 角色。",
        parameters={"example_id": "必填，样例 id"},
        run=_delete_case,
        dangerous=True,
    ),
    "batch_delete_cases": Tool(
        name="batch_delete_cases",
        description="批量删除样例。不可逆，需二次确认，且要求 admin 角色。",
        parameters={"example_ids": "必填，样例 id 字符串数组，非空"},
        run=_batch_delete_cases,
        dangerous=True,
    ),
    "delete_evaluator": Tool(
        name="delete_evaluator",
        description="删除评估器实例。不可逆，需二次确认。",
        parameters={"evaluator_id": "必填，评估器 id"},
        run=_delete_evaluator,
        dangerous=True,
    ),
    "delete_provider": Tool(
        name="delete_provider",
        description="删除 judge provider。不可逆，需二次确认。",
        parameters={"provider_id": "必填，provider id"},
        run=_delete_provider,
        dangerous=True,
    ),
    "delete_run": Tool(
        name="delete_run",
        description="删除一次评估运行记录。不可逆，需二次确认。",
        parameters={"run_id": "必填，运行 ID"},
        run=_delete_run,
        dangerous=True,
    ),
    "delete_scheduled_task": Tool(
        name="delete_scheduled_task",
        description="删除一个定时评估任务。不可逆，需二次确认，且要求 admin 角色。",
        parameters={"task_id": "必填，定时任务 id"},
        run=_delete_scheduled_task,
        dangerous=True,
    ),
    "delete_category": Tool(
        name="delete_category",
        description="删除一个项目类别（该类别下有样例时会被后端拒绝）。不可逆，需二次确认，且要求 admin 角色。",
        parameters={"category_id": "必填，类别 id"},
        run=_delete_category,
        dangerous=True,
    ),
    "archive_dataset": Tool(
        name="archive_dataset",
        description="归档数据集（归档后禁止新导入，但历史数据保留）。需二次确认。",
        parameters={"name": "必填，数据集名"},
        run=_archive_dataset,
        dangerous=True,
    ),
    "activate_dataset": Tool(
        name="activate_dataset",
        description="重新激活一个已归档的数据集。需二次确认。",
        parameters={"name": "必填,数据集名"},
        run=_activate_dataset,
        dangerous=True,
    ),
    "dedupe_dataset": Tool(
        name="dedupe_dataset",
        description=(
            "对数据集去重。strategy：skip（跳过重复，默认）/ replace（保留最新）/ "
            "append_suffix（重命名保留）。不可逆，需二次确认。"
        ),
        parameters={
            "name": "必填，数据集名",
            "strategy": "可选，skip / replace / append_suffix，默认 skip",
        },
        run=_dedupe_dataset,
        dangerous=True,
    ),
}


def tools_catalog() -> str:
    """把工具清单渲染成给编排 LLM 的文本（name + description + params + 危险标注）。"""
    lines: list[str] = []
    for t in TOOLS.values():
        params = ", ".join(f"{k}({v})" for k, v in t.parameters.items()) or "无"
        danger = "【危险·需二次确认】" if t.dangerous else ""
        lines.append(f"- {t.name}: {danger}{t.description} 参数: {params}")
    return "\n".join(lines)
