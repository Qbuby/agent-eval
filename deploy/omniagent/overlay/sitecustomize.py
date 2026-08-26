"""Register Agent Eval's optional OmniAgent tool overlay at interpreter startup."""

from __future__ import annotations

import logging
import os


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


if _enabled(os.environ.get("OMNIAGENT_AXI_TOOLS_ENABLED")):
    try:
        from omniagent.tools.registry import ToolRegistry
        from omniagent_overlay.axi_tools import provide_tools

        ToolRegistry.register("agent_eval_axi", provide_tools)
    except Exception:
        logging.getLogger("agent-eval-omniagent-overlay").exception(
            "failed to register the Agent Eval Axi overlay"
        )
