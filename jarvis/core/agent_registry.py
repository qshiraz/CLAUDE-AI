"""Agent registry — discover, register, and expose all Jarvis agents/plugins."""

import importlib
import importlib.util
import inspect
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Central registry that maps agent names to instances and exposes their tools."""

    def __init__(self) -> None:
        self._agents: dict[str, "BaseAgent"] = {}

    # ── Registration ────────────────────────────────────────────────────────

    def register(self, agent: "BaseAgent") -> None:
        if agent.name in self._agents:
            logger.warning("Agent '%s' already registered — overwriting.", agent.name)
        self._agents[agent.name] = agent
        logger.info("Registered agent: %s", agent.name)

    def unregister(self, name: str) -> None:
        self._agents.pop(name, None)

    # ── Discovery ───────────────────────────────────────────────────────────

    def load_builtin_agents(self, cfg: dict) -> None:
        """Import and register every enabled built-in agent."""
        from jarvis.agents.weather_agent import WeatherAgent
        from jarvis.agents.smart_home_agent import SmartHomeAgent
        from jarvis.agents.email_agent import EmailAgent
        from jarvis.agents.news_agent import NewsAgent
        from jarvis.agents.flight_agent import FlightAgent
        from jarvis.agents.traffic_agent import TrafficAgent
        from jarvis.agents.education_agent import EducationAgent
        from jarvis.agents.camera_agent import CameraAgent
        from jarvis.agents.radio_agent import RadioAgent
        from jarvis.agents.sports_agent import SportsAgent

        builtin_map = {
            "weather": WeatherAgent,
            "smart_home": SmartHomeAgent,
            "email": EmailAgent,
            "news": NewsAgent,
            "flights": FlightAgent,
            "traffic": TrafficAgent,
            "education": EducationAgent,
            "camera": CameraAgent,
            "radio": RadioAgent,
            "sports": SportsAgent,
        }

        for key, cls in builtin_map.items():
            agent_cfg = cfg.get(key, {})
            if agent_cfg.get("enabled", True):
                try:
                    self.register(cls(agent_cfg, cfg))
                except Exception as exc:
                    logger.warning("Could not load agent '%s': %s", key, exc)

    def load_plugins(self, plugin_dir: Path) -> None:
        """Dynamically load any BaseAgent subclasses found in *plugin_dir*."""
        if not plugin_dir.exists():
            return

        from jarvis.agents.base_agent import BaseAgent

        for py_file in sorted(plugin_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            module_name = f"jarvis.plugins.{py_file.stem}"
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)  # type: ignore[attr-defined]
            except Exception as exc:
                logger.warning("Plugin '%s' failed to load: %s", py_file.name, exc)
                continue

            for _, obj in inspect.getmembers(module, inspect.isclass):
                if issubclass(obj, BaseAgent) and obj is not BaseAgent:
                    try:
                        self.register(obj({}, {}))
                        logger.info("Loaded plugin agent: %s", obj.__name__)
                    except Exception as exc:
                        logger.warning("Plugin agent '%s' init failed: %s", obj.__name__, exc)

    # ── Tool export ─────────────────────────────────────────────────────────

    def all_tools(self) -> list[dict]:
        """Return all tool schemas from all registered agents (used by Claude)."""
        tools: list[dict] = []
        for agent in self._agents.values():
            tools.extend(agent.tools())
        return tools

    def dispatch(self, tool_name: str, tool_input: dict) -> str:
        """Route a tool call from Claude to the owning agent."""
        for agent in self._agents.values():
            if tool_name in agent.tool_names():
                try:
                    return agent.handle(tool_name, tool_input)
                except Exception as exc:
                    logger.exception("Agent '%s' tool '%s' raised", agent.name, tool_name)
                    return f"Error: {exc}"
        return f"Unknown tool: {tool_name}"

    # ── Helpers ─────────────────────────────────────────────────────────────

    @property
    def agents(self) -> list["BaseAgent"]:
        return list(self._agents.values())

    def agent_names(self) -> list[str]:
        return list(self._agents.keys())

    def __repr__(self) -> str:
        return f"<AgentRegistry agents={self.agent_names()}>"
