"""
Example Jarvis plugin — copy this file, rename it, and implement your agent.

Drop any file matching *.py (that doesn't start with _) into jarvis/plugins/
and Jarvis will auto-load it on startup. No registration required.

Your class must:
  1. Inherit from BaseAgent
  2. Set a unique `name` property
  3. Implement `tools()` returning Claude tool-schema dicts
  4. Implement `handle(tool_name, tool_input)` returning a string
"""

import json
from typing import Any

from jarvis.agents.base_agent import BaseAgent


class ExamplePlugin(BaseAgent):
    name = "example"
    description = "Example plugin — replace this with your own agent."

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "say_hello",
                "description": "Returns a hello message from the example plugin.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Name to greet."}
                    },
                    "required": [],
                },
            }
        ]

    def handle(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name == "say_hello":
            name = tool_input.get("name", "World")
            return json.dumps({"message": f"Hello, {name}! This is the example plugin."})
        return f"Unknown tool: {tool_name}"
