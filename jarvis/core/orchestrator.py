"""Jarvis orchestrator — drives Claude with all registered agent tools."""

import asyncio
import json
import logging
from typing import AsyncIterator

import anthropic

from jarvis.core.agent_registry import AgentRegistry
from jarvis.core.memory import MemoryManager

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are Sahil, an elite AI personal assistant built for {user_name}. \
You have specialist tools for weather, smart home, email, news, flights, traffic, \
education/tutoring, and can be extended with plugins.

Personality: brilliant, proactive, slightly witty — you know your user deeply \
and personalise every response.

Guidelines:
- Use tools for all live data — never guess temperatures, scores, or facts.
- Summarise data intelligently — no raw JSON dumps.
- When you learn something important about the user or a student, remember it using \
  the memory system by calling the remember_fact tool.
- Address the user as "{user_name}".
- For education: be patient, encouraging, use Kenya/East Africa real-world examples.

{memory_context}
"""

REMEMBER_TOOL = {
    "name": "remember_fact",
    "description": (
        "Saves an important fact to Sahil's long-term memory. "
        "Use this whenever you learn something significant about the user — "
        "their preferences, important dates, decisions, or anything they ask you to remember."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": "Category e.g. 'personal', 'preference', 'health', 'work', 'family', 'reminder'",
            },
            "key": {"type": "string", "description": "Short label e.g. 'favourite_food', 'wife_name'"},
            "value": {"type": "string", "description": "The fact to remember"},
            "importance": {
                "type": "integer",
                "description": "1=low, 2=medium, 3=high importance",
            },
        },
        "required": ["category", "key", "value"],
    },
}


class JarvisOrchestrator:
    MAX_HISTORY = 20  # keep last 20 messages to avoid context bloat

    def __init__(self, cfg: dict, registry: AgentRegistry) -> None:
        self.cfg = cfg
        self.registry = registry
        self.memory = MemoryManager()
        self.client = anthropic.Anthropic()
        self.model: str = cfg.get("claude", {}).get("model", "claude-sonnet-4-6")
        self.user_name: str = cfg.get("jarvis", {}).get("user_name", "Boss")
        self.history: list[dict] = []

    # ── Public API ──────────────────────────────────────────────────────────

    async def chat(self, user_message: str) -> AsyncIterator[str]:
        self.memory.log_conversation("user", user_message)
        self.history.append({"role": "user", "content": user_message})
        full_response: list[str] = []
        async for chunk in self._run_agent_loop():
            full_response.append(chunk)
            yield chunk
        response_text = "".join(full_response)
        if response_text:
            self.memory.log_conversation("jarvis", response_text)

    def reset(self) -> None:
        self.history.clear()

    # ── Internal loop ───────────────────────────────────────────────────────

    def _run_agent_loop(self) -> "AsyncIterator[str]":
        return self._agent_loop_impl()

    async def _agent_loop_impl(self) -> AsyncIterator[str]:  # type: ignore[return-value]
        agent_tools = self.registry.all_tools()
        all_tools = agent_tools + [REMEMBER_TOOL]
        memory_ctx = self.memory.to_context_string()
        system = SYSTEM_PROMPT.format(user_name=self.user_name, memory_context=memory_ctx)

        while True:
            text_parts: list[str] = []
            tool_calls: list[dict] = []

            # Trim history to avoid context bloat
            if len(self.history) > self.MAX_HISTORY:
                self.history = self.history[-self.MAX_HISTORY:]

            with self.client.messages.stream(
                model=self.model,
                max_tokens=1024,
                system=system,
                messages=self.history,
                tools=all_tools if all_tools else anthropic.NOT_GIVEN,
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

            # Dispatch all tool calls in parallel
            async def _run_tool(tc: dict) -> dict:
                logger.debug("Dispatching tool: %s(%s)", tc["name"], tc["input"])
                if tc["name"] == "remember_fact":
                    inp = tc["input"]
                    self.memory.remember(
                        inp.get("category", "general"),
                        inp.get("key", "fact"),
                        inp.get("value", ""),
                        int(inp.get("importance", 1)),
                    )
                    result = json.dumps({"remembered": True, "key": inp.get("key")})
                else:
                    result = await asyncio.to_thread(self.registry.dispatch, tc["name"], tc["input"])
                return {"type": "tool_result", "tool_use_id": tc["id"], "content": result}

            tool_results = list(await asyncio.gather(*[_run_tool(tc) for tc in tool_calls]))

            # Emit UI action events for any tool results that carry an action
            for tr in tool_results:
                try:
                    obj = json.loads(tr["content"])
                    if isinstance(obj, dict) and "action" in obj:
                        yield f'\x00ACTION:{tr["content"]}\x00'
                except Exception:
                    pass

            self.history.append({"role": "user", "content": tool_results})

    @staticmethod
    def _build_assistant_content(
        text: str, tool_calls: list[dict], final_msg: object | None = None
    ) -> list[dict]:
        blocks = []
        if final_msg is not None:
            for block in final_msg.content:  # type: ignore[attr-defined]
                if block.type == "thinking":
                    blocks.append({"type": "thinking", "thinking": block.thinking, "signature": block.signature})
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
