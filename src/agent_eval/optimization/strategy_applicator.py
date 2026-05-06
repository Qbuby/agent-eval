from __future__ import annotations

import copy
import logging
from typing import Any

from agent_eval.models.optimization import OptimizationStrategy, StrategyChange

logger = logging.getLogger(__name__)


class StrategyApplicator:
    def apply(self, config: dict[str, Any], strategy: OptimizationStrategy) -> dict[str, Any]:
        new_config = copy.deepcopy(config)

        for change in strategy.changes:
            try:
                self._apply_change(new_config, change)
            except Exception as e:
                logger.error("Failed to apply change %s -> %s: %s", change.change_type, change.target, e)

        return new_config

    def _apply_change(self, config: dict[str, Any], change: StrategyChange) -> None:
        if change.change_type == "prompt_modification":
            config["system_prompt"] = change.after

        elif change.change_type == "tool_description":
            tool_name = change.target.replace("tool.", "").replace(".description", "")
            tools = config.get("tools", [])
            for tool in tools:
                if tool.get("name") == tool_name:
                    tool["description"] = change.after
                    break
            else:
                logger.warning("Tool '%s' not found in config", tool_name)

        elif change.change_type == "tool_parameter":
            parts = change.target.split(".")
            if len(parts) >= 3 and parts[0] == "tool":
                tool_name = parts[1]
                param_name = ".".join(parts[2:])
                tools = config.get("tools", [])
                for tool in tools:
                    if tool.get("name") == tool_name:
                        tool.setdefault("parameters", {})[param_name] = change.after
                        break

        elif change.change_type == "system_parameter":
            config[change.target] = self._coerce_value(change.after)

    @staticmethod
    def _coerce_value(value: str) -> Any:
        if value.lower() in ("true", "false"):
            return value.lower() == "true"
        try:
            return int(value)
        except ValueError:
            pass
        try:
            return float(value)
        except ValueError:
            pass
        return value
