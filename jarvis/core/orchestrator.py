"""Jarvis orchestrator — drives Claude with all registered agent tools."""

import json
import logging
from typing import AsyncIterator

import anthropic

from jarvis.core.agent_registry import AgentRegistry

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are Jarvis, an advanced AI personal assistant. You have access to a suite of \
specialist tools covering weather, smart home control, email, news, flight tracking, \
and live traffic — and the user can extend you further with plugins.

Personality: helpful, concise, proactive, slightly witty — think Friday from Iron Man.

Guidelines:
- Always use the relevant tool(s) to fetch live data; never guess values like temperatures, \
  flight numbers, or traffic conditions.
- When multiple tools are needed for one request, call them efficiently.
- Summarise data intelligently — don't dump raw JSON at the user.
- If a tool returns an error (API key missing, service down), acknowledge it gracefully and \
  offer alternatives.
- Address the user as "{user_name}".
"""


class JarvisOrchestrator:
    def __init__(self, cfg: dict, registry: AgentRegistry) -> None:
        self.cfg = cfg
        self.registry = registry
        self.client = anthropic.Anthropic()
        self.model: str = cfg.get("claude", {}).get("model", "claude-opus-4-8")
        self.user_name: str = cfg.get("jarvis", {}).get("user_name", "Boss")
        self.history: list[dict] = []

    # ── Public API ──────────────────────────────────────────────────────────

    async def chat(self, user_message: str) -> AsyncIterator[str]:
        """Send a user message and yield streamed text chunks back."""
        self.history.append({"role": "user", "content": user_message})
        async for chunk in self._run_agent_loop():
            yield chunk

    def reset(self) -> None:
        self.history.clear()

    # ── Internal loop ───────────────────────────────────────────────────────

    def _run_agent_loop(self) -> "AsyncIterator[str]":
        return self._agent_loop_impl()

    async def _agent_loop_impl(self) -> AsyncIterator[str]:  # type: ignore[return-value]
        tools = self.registry.all_tools()
        system = SYSTEM_PROMPT.format(user_name=self.user_name)

        while True:
            text_parts: list[str] = []
            tool_calls: list[dict] = []

            with self.client.messages.stream(
                model=self.model,
                max_tokens=4096,
                system=system,
                messages=self.history,
                tools=tools if tools else anthropic.NOT_GIVEN,
                thinking={"type": "adaptive"},
            ) as stream:
                for event in stream:
                    if (
                        hasattr(event, "type")
                        and event.type == "content_block_delta"
                        and hasattr(event.delta, "type")
                        and event.delta.type == "text_delta"
                    ):
                        chunk = event.delta.text
                        text_parts.append(chunk)
                        yield chunk

                final = stream.get_final_message()

            # Collect tool-use blocks from the final message
            for block in final.content:
                if block.type == "tool_use":
                    tool_calls.append(
                        {"id": block.id, "name": block.name, "input": block.input}
                    )

            full_text = "".join(text_parts)

            if not tool_calls:
                self.history.append({"role": "assistant", "content": full_text or " "})
                return

            self.history.append(
                {
                    "role": "assistant",
                    "content": self._build_assistant_content(full_text, tool_calls, final),
                }
            )

            tool_results = []
            for tc in tool_calls:
                logger.debug("Dispatching tool: %s(%s)", tc["name"], tc["input"])
                result = self.registry.dispatch(tc["name"], tc["input"])
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tc["id"],
                        "content": result,
                    }
                )

            self.history.append({"role": "user", "content": tool_results})

    @staticmethod
    def _build_assistant_content(
        text: str, tool_calls: list[dict], final_msg: object | None = None
    ) -> list[dict]:
        blocks = []
        if final_msg is not None:
            for block in final_msg.content:  # type: ignore[attr-defined]
                if block.type == "thinking":
                    blocks.append({"type": "thinking", "thinking": block.thinking})
                elif block.type == "text" and block.text:
                    blocks.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    )
            return blocks

        if text:
            blocks.append({"type": "text", "text": text})
        for tc in tool_calls:
            blocks.append(
                {
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc["input"],
                }
            )
        return blocks
