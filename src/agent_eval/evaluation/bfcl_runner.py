from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ALL_SUBSETS = [
    "simple_python", "simple_java", "simple_javascript",
    "multiple", "parallel", "parallel_multiple",
    "irrelevance",
    "live_simple", "live_multiple", "live_parallel", "live_parallel_multiple",
    "live_irrelevance", "live_relevance",
    "multi_turn_base", "multi_turn_miss_func", "multi_turn_miss_param", "multi_turn_long_context",
    "web_search_base", "web_search_no_snippet",
    "memory_kv", "memory_vector", "memory_rec_sum",
]

CATEGORY_PRESETS = {
    "non_live": ["simple_python", "simple_java", "simple_javascript", "multiple", "parallel", "parallel_multiple"],
    "live": ["live_simple", "live_multiple", "live_parallel", "live_parallel_multiple"],
    "multi_turn": ["multi_turn_base", "multi_turn_miss_func", "multi_turn_miss_param", "multi_turn_long_context"],
    "agentic": ["web_search_base", "web_search_no_snippet", "memory_kv", "memory_vector", "memory_rec_sum"],
    "simple": ["simple_python", "multiple", "parallel", "parallel_multiple"],
    "all": ALL_SUBSETS,
}


@dataclass
class BFCLConfig:
    model: str = ""
    api_url: str = ""
    api_key: str = ""
    categories: list[str] = field(default_factory=lambda: ["simple_python", "multiple", "parallel"])
    concurrency: int = 5
    fc_model: bool = True
    underscore_to_dot: bool = True
    temperature: float = 0.0
    serpapi_key: str = ""
    output_dir: str = "./bfcl_results"
    limit: int | None = None

    @classmethod
    def from_yaml(cls, path: str | Path) -> BFCLConfig:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if "categories" in data and isinstance(data["categories"], str):
            data["categories"] = [c.strip() for c in data["categories"].split(",")]
        return cls(**data)

    def resolve_subsets(self) -> list[str]:
        subsets = []
        for cat in self.categories:
            if cat in CATEGORY_PRESETS:
                subsets.extend(CATEGORY_PRESETS[cat])
            elif cat in ALL_SUBSETS:
                subsets.append(cat)
        return list(dict.fromkeys(subsets))


def build_task_config(cfg: BFCLConfig) -> Any:
    """Build an EvalScope TaskConfig from our BFCLConfig."""
    from evalscope import TaskConfig

    subsets = cfg.resolve_subsets()

    extra_params: dict[str, Any] = {
        "underscore_to_dot": cfg.underscore_to_dot,
        "is_fc_model": cfg.fc_model,
    }
    if cfg.serpapi_key:
        extra_params["SERPAPI_API_KEY"] = cfg.serpapi_key
    elif os.environ.get("SERPAPI_API_KEY"):
        extra_params["SERPAPI_API_KEY"] = os.environ["SERPAPI_API_KEY"]

    task_cfg = TaskConfig(
        model=cfg.model,
        api_url=cfg.api_url,
        api_key=cfg.api_key,
        eval_type="openai_api",
        datasets=["bfcl_v4"],
        eval_batch_size=cfg.concurrency,
        dataset_args={
            "bfcl_v4": {
                "subset_list": subsets,
                "extra_params": extra_params,
            }
        },
        generation_config={"temperature": cfg.temperature},
        use_cache=cfg.output_dir,
    )

    if cfg.limit:
        task_cfg.limit = cfg.limit

    return task_cfg


def run_bfcl(cfg: BFCLConfig) -> dict[str, Any]:
    """Run BFCL-v4 evaluation and return results summary."""
    from evalscope import run_task

    task_cfg = build_task_config(cfg)
    results = run_task(task_cfg=task_cfg)
    return results
