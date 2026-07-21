"""双模对比评估的纯函数：位置随机化/还原 + 对比汇总。

对比评估把「两次独立打分求差」变成「一次相对判断」以消除 judge 跨调用方差。
本模块只做无 I/O 的纯计算，便于单测：

* ``pick_swap`` —— 决定本样例是否交换 A/B 在 prompt 中的呈现顺序（消除位置偏见）。
* ``restore_verdict`` —— judge 按 slot 顺序（可能已交换）返回 verdict 后，
  按 swap 标记把结论还原到真实 A/B（winner 翻转、score_a↔score_b 互换）。
* ``build_comparison_summary`` —— 把逐样例 verdict 汇总成 run 级对比统计。

winner 取值固定为 ``"A" | "B" | "tie"``。
"""
from __future__ import annotations

import random
from typing import Any

_FLIP = {"A": "B", "B": "A", "tie": "tie"}


def pick_swap(rng: random.Random | None = None) -> bool:
    """本样例是否交换 A/B 呈现顺序。默认用模块级随机源，测试可注入种子 rng。"""
    r = rng or random
    return r.random() < 0.5


def _flip_winner(w: Any) -> str:
    return _FLIP.get(str(w or "tie"), "tie")


def restore_verdict(verdict: dict[str, Any], *, swapped: bool) -> dict[str, Any]:
    """把 judge 按 slot 顺序给出的 verdict 还原成真实 A/B。

    ``swapped=False`` 时 slot1=A、slot2=B，verdict 已是真实视角，原样返回（深拷贝
    保证不改入参）。``swapped=True`` 时 judge 眼中的 "A" 其实是真实 B，故：
      * 每维度 score_a↔score_b 互换、winner A↔B 翻转；
      * overall_winner 翻转。
    ``tie`` 不受影响。
    """
    if not isinstance(verdict, dict):
        return {"dimensions": [], "overall_winner": "tie", "reasoning": ""}

    dims_in = verdict.get("dimensions")
    dims: list[dict[str, Any]] = []
    if isinstance(dims_in, list):
        for d in dims_in:
            if not isinstance(d, dict):
                continue
            sa = d.get("score_a")
            sb = d.get("score_b")
            win = str(d.get("winner") or "tie")
            if swapped:
                sa, sb = sb, sa
                win = _flip_winner(win)
            dims.append({
                "name": str(d.get("name") or ""),
                "score_a": sa,
                "score_b": sb,
                "winner": win,
                "reason": str(d.get("reason") or ""),
            })

    overall = verdict.get("overall_winner")
    overall_winner = _flip_winner(overall) if swapped else str(overall or "tie")

    return {
        "dimensions": dims,
        "overall_winner": overall_winner,
        "reasoning": str(verdict.get("reasoning") or ""),
    }


def _evaluator_key(entry: dict[str, Any]) -> str:
    """返回跨样例稳定的 evaluator 身份键，优先使用不可变版本 ID。"""
    for field_name in ("evaluator_version_id", "evaluator_id", "tag", "label"):
        value = entry.get(field_name)
        if value:
            return str(value)
    return "legacy"


def _summary_entry(entry: dict[str, Any], *, legacy: bool = False) -> dict[str, Any]:
    return {
        "evaluator_key": _evaluator_key(entry),
        "evaluator_id": entry.get("evaluator_id"),
        "evaluator_version_id": entry.get("evaluator_version_id"),
        "label": entry.get("label") or ("legacy" if legacy else "comparison"),
        "tag": entry.get("tag") or ("legacy" if legacy else None),
        "legacy": legacy,
        "total": 0,
        "scored": 0,
        "evaluation_errors": 0,
        "a_wins": 0,
        "b_wins": 0,
        "ties": 0,
        "per_dimension": {},
        "_per_dimension": {},
    }


def build_comparison_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """按 evaluator 独立汇总逐样例 comparative 结果。

    新 payload 优先读取 ``comparison.evaluator_verdicts``；只有该字段不存在时才
    回退 deprecated ``comparison.verdict``，并将其归入 ``legacy`` 组。跨 evaluator
    的同名 dimension 永不合并。为兼容旧消费方，只有最终恰好一个 evaluator 组时，
    才把该组的胜负与维度统计映射到旧顶层字段。
    """
    groups: dict[str, dict[str, Any]] = {}

    for row in rows:
        comp = row.get("comparison") if isinstance(row, dict) else None
        if not isinstance(comp, dict):
            continue

        raw_entries = comp.get("evaluator_verdicts")
        entries: list[tuple[dict[str, Any], bool]] = []
        if isinstance(raw_entries, list):
            entries.extend((entry, False) for entry in raw_entries if isinstance(entry, dict))
        else:
            legacy_verdict = comp.get("verdict")
            if isinstance(legacy_verdict, dict):
                entries.append(({
                    "label": "legacy",
                    "tag": "legacy",
                    "status": "scored",
                    "verdict": legacy_verdict,
                }, True))

        for entry, legacy in entries:
            key = _evaluator_key(entry)
            group = groups.setdefault(key, _summary_entry(entry, legacy=legacy))
            group["total"] += 1

            verdict = entry.get("verdict")
            if entry.get("status") != "scored" or not isinstance(verdict, dict):
                group["evaluation_errors"] += 1
                continue

            group["scored"] += 1
            overall = str(verdict.get("overall_winner") or "tie")
            if overall == "A":
                group["a_wins"] += 1
            elif overall == "B":
                group["b_wins"] += 1
            else:
                group["ties"] += 1

            dimensions = verdict.get("dimensions")
            if not isinstance(dimensions, list):
                continue
            for dimension in dimensions:
                if not isinstance(dimension, dict):
                    continue
                name = str(dimension.get("name") or "总体")
                slot = group["_per_dimension"].setdefault(name, {
                    "a_wins": 0, "b_wins": 0, "ties": 0,
                    "sum_a": 0.0, "sum_b": 0.0, "n": 0,
                })
                winner = str(dimension.get("winner") or "tie")
                if winner == "A":
                    slot["a_wins"] += 1
                elif winner == "B":
                    slot["b_wins"] += 1
                else:
                    slot["ties"] += 1
                try:
                    slot["sum_a"] += float(dimension.get("score_a"))
                    slot["sum_b"] += float(dimension.get("score_b"))
                    slot["n"] += 1
                except (TypeError, ValueError):
                    pass

    evaluators: list[dict[str, Any]] = []
    for group in groups.values():
        per_dimension: dict[str, Any] = {}
        for name, slot in group.pop("_per_dimension").items():
            n = int(slot["n"])
            per_dimension[name] = {
                "a_wins": int(slot["a_wins"]),
                "b_wins": int(slot["b_wins"]),
                "ties": int(slot["ties"]),
                "mean_a": round(slot["sum_a"] / n, 4) if n else None,
                "mean_b": round(slot["sum_b"] / n, 4) if n else None,
                "n": n,
            }
        group["per_dimension"] = per_dimension
        evaluators.append(group)

    summary: dict[str, Any] = {"evaluators": evaluators}
    if len(evaluators) == 1:
        only = evaluators[0]
        summary.update({
            "total": only["scored"],
            "a_wins": only["a_wins"],
            "b_wins": only["b_wins"],
            "ties": only["ties"],
            "per_dimension": only["per_dimension"],
        })
    return summary


__all__ = ["pick_swap", "restore_verdict", "build_comparison_summary"]
