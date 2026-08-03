"""从参考答案提炼「答案关键点」，回填三类数据源（CLI 壳）。

提炼逻辑已抽进 ``agent_eval.evaluation.key_points_extractor`` service，本脚本
只是它的命令行入口——全量幂等回填，tenant 上下文为 None（旁路不过滤，覆盖所有
租户的行）。HTTP 路由的「提炼关键点」按钮走同一个 service 的异步 job。

用法
----
    # 试跑：只取 3 条，打印提炼结果，不写库
    python extract_key_points.py --target benchmark --limit 3 --dry-run

    # 全量回填（幂等，只处理关键点为空的行）
    python extract_key_points.py --target benchmark

    # 三类一起
    python extract_key_points.py --target all

幂等性
------
只挑选「有答案且关键点为空」的行，重跑不会覆盖已有关键点，也不会重复计费。
中断后直接重跑即可续上。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from agent_eval.evaluation.key_points_extractor import (
    DEFAULT_CONCURRENCY,
    DEFAULT_PROVIDER_NAME,
    MIN_ANSWER_CHARS,
    TARGETS,
    ExtractionError,
    Unit,
    run_extraction,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("extract_key_points")
logger.setLevel(logging.INFO)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--target",
        required=True,
        choices=[*TARGETS, "all"],
        help="提炼目标数据源",
    )
    ap.add_argument("--limit", type=int, default=None, help="最多处理多少条（试跑用）")
    ap.add_argument("--dry-run", action="store_true", help="只提炼并打印，不写库")
    ap.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    ap.add_argument("--provider", default=DEFAULT_PROVIDER_NAME)
    ap.add_argument("--model", default=None, help="覆盖 provider 的 default_model")
    ap.add_argument("--show", type=int, default=3, help="打印前 N 条提炼结果供人工核验")
    ap.add_argument(
        "--count-only", action="store_true", help="只统计待提炼数量，不调 LLM 不写库"
    )
    args = ap.parse_args()

    targets = list(TARGETS) if args.target == "all" else [args.target]

    # --count-only：只采集不提炼。走 dry_run 且把 provider 调用短路——run_extraction
    # 在 pending=0 时直接返回，否则会真的调 LLM。这里单独处理以避免计费。
    if args.count_only:
        from agent_eval.data.langfuse_provider import build_langfuse_client
        from agent_eval.evaluation import key_points_extractor as kpe
        from agent_eval.data.langfuse_provider import LangfuseDatasetProvider

        units: list[Unit] = []
        for t in targets:
            if t == "candidate":
                got = await kpe.collect_candidate(args.limit)
            elif t == "benchmark":
                got = await kpe.collect_benchmark(args.limit)
            else:
                prov = LangfuseDatasetProvider(await build_langfuse_client())
                got, _ = await kpe.collect_multichat(prov, args.limit)
            units.extend(u for u in got if not kpe.too_short(u.answer))
        print(f"PENDING={len(units)}")
        by_kind: dict[str, int] = {}
        for u in units:
            by_kind[u.kind] = by_kind.get(u.kind, 0) + 1
        for k, n in sorted(by_kind.items()):
            print(f"  {k}={n}")
        return 0

    done = 0

    def _on_progress(d: int, total: int, unit: Unit) -> None:
        nonlocal done
        done = d
        if d % 50 == 0 or d == total:
            logger.info("进度 %d/%d", d, total)

    try:
        result = await run_extraction(
            targets=targets,
            limit=args.limit,
            provider_name=args.provider,
            model=args.model,
            concurrency=args.concurrency,
            dry_run=args.dry_run,
            on_progress=_on_progress,
        )
    except ExtractionError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    if result.skipped_short:
        logger.info(
            "跳过过短答案(<%d字符) %d 条", MIN_ANSWER_CHARS, result.skipped_short
        )

    if result.pending == 0:
        print("PENDING=0 无待提炼样例")
        return 0

    total_chars = sum(len(u.answer) for u in result.units)
    print(f"PENDING={result.pending} TOTAL_CHARS={total_chars}")

    ok_units = [u for u in result.units if u.points]
    failed = [u for u in result.units if not u.points]

    # 人工核验样本：提炼质量只能靠眼睛看，打印几条完整结果。
    for u in ok_units[: args.show]:
        print(f"\n--- {u.label}")
        print(f"    答案长度={len(u.answer)}")
        for i, p in enumerate(u.points, 1):
            print(f"    {i}. {p}")

    if failed:
        print(f"\n=== 失败 {len(failed)} 条（前 5）===")
        for u in failed[:5]:
            print(f"  {u.label}: {u.error}")

    if args.dry_run:
        print(f"\nDRY_RUN EXTRACTED={result.extracted} FAILED={result.failed} 未写库")
        return 0

    write_failed = [u for u in ok_units if u.error]
    print(
        f"\nEXTRACTED={result.extracted} FAILED={result.failed} "
        f"WRITTEN={result.written} WRITE_FAILED={result.write_failed}"
    )
    for u in write_failed[:5]:
        print(f"  写回失败 {u.label}: {u.error}")
    return 0 if not failed and not write_failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
