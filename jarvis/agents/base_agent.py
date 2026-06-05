"""BaseAgent — all Jarvis agents and plugins must subclass this."""

import abc
from typing import Any


class BaseAgent(abc.ABC):
    """Abstract base for every Jarvis agent.

    Subclasses must implement:
      - name (property)   : unique slug, e.g. "weather"
      - description       : one-sentence description used in logs/UI
      - tools()           : list of Claude tool-schema dicts
      - handle()          : dispatch a tool call to the correct method
    """

    def __init__(self, agent_cfg: dict[str, Any], global_cfg: dict[str, Any]) -> None:
        self.cfg = agent_cfg
        self.global_cfg = global_cfg

    @property
    @abc.abstractmethod
    def name(self) -> str: ...

    @property
    @abc.abstractmethod
    def description(self) -> str: ...

    @abc.abstractmethod
    def tools(self) -> list[dict[str, Any]]:
        """Return list of Claude tool-schema dicts."""

    @abc.abstractmethod
    def handle(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        """Execute the named tool and return a string result."""

    def tool_names(self) -> list[str]:
        return [t["name"] for t in self.tools()]

    # ── Convenience helpers ─────────────────────────────────────────────────

    @property
    def location(self) -> dict[str, Any]:
        return self.global_cfg.get("jarvis", {}).get("location", {})

    def _city(self) -> str:
        return self.location.get("city", "unknown")

    def _lat(self) -> float:
        return float(self.location.get("latitude", 0))

    def _lon(self) -> float:
        return float(self.location.get("longitude", 0))
