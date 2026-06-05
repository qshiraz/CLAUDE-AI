"""News agent — local and international headlines via NewsAPI."""

import json
from typing import Any

import requests

from jarvis.agents.base_agent import BaseAgent


class NewsAgent(BaseAgent):
    name = "news"
    description = "Fetches top headlines and international news from NewsAPI."

    def __init__(self, agent_cfg: dict, global_cfg: dict) -> None:
        super().__init__(agent_cfg, global_cfg)
        self._api_key: str = agent_cfg.get("api_key", "")
        self._country: str = agent_cfg.get("country", "pk")
        self._page_size: int = int(agent_cfg.get("page_size", 10))
        self._base = "https://newsapi.org/v2"

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "get_local_news",
                "description": "Returns top local headlines for the user's country.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "count": {
                            "type": "integer",
                            "description": "Number of headlines to return (max 10). Default 5.",
                        }
                    },
                    "required": [],
                },
            },
            {
                "name": "get_international_news",
                "description": "Returns top international headlines or news about a specific topic.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "Optional topic keyword, e.g. 'technology', 'economy'.",
                        },
                        "count": {"type": "integer"},
                    },
                    "required": [],
                },
            },
        ]

    def handle(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        count = min(int(tool_input.get("count", 5)), self._page_size)
        if tool_name == "get_local_news":
            return self._local_news(count)
        if tool_name == "get_international_news":
            return self._international_news(tool_input.get("topic", ""), count)
        return f"Unknown tool: {tool_name}"

    def _local_news(self, count: int) -> str:
        if not self._api_key or self._api_key.startswith("YOUR_"):
            return self._demo_news("local", count)
        try:
            r = requests.get(
                f"{self._base}/top-headlines",
                params={
                    "country": self._country,
                    "pageSize": count,
                    "apiKey": self._api_key,
                },
                timeout=8,
            )
            r.raise_for_status()
            return self._format_articles(r.json().get("articles", []))
        except Exception as exc:
            return f"News API error: {exc}"

    def _international_news(self, topic: str, count: int) -> str:
        if not self._api_key or self._api_key.startswith("YOUR_"):
            return self._demo_news("international" + (f"/{topic}" if topic else ""), count)
        try:
            params: dict = {"pageSize": count, "apiKey": self._api_key}
            if topic:
                params["q"] = topic
                endpoint = f"{self._base}/everything"
                params["sortBy"] = "publishedAt"
                params["language"] = "en"
            else:
                endpoint = f"{self._base}/top-headlines"
                params["sources"] = "bbc-news,reuters,al-jazeera-english"
            r = requests.get(endpoint, params=params, timeout=8)
            r.raise_for_status()
            return self._format_articles(r.json().get("articles", []))
        except Exception as exc:
            return f"News API error: {exc}"

    @staticmethod
    def _format_articles(articles: list[dict]) -> str:
        formatted = [
            {
                "title": a.get("title", ""),
                "source": a.get("source", {}).get("name", ""),
                "published": a.get("publishedAt", "")[:10],
                "summary": (a.get("description") or "")[:200],
                "url": a.get("url", ""),
            }
            for a in articles
        ]
        return json.dumps({"articles": formatted})

    def _demo_news(self, category: str, count: int) -> str:
        articles = [
            {
                "title": f"[Demo] {category.capitalize()} Story #{i+1}",
                "source": "Demo News",
                "published": "2026-06-05",
                "summary": "Configure NewsAPI key in config.local.yaml to see live headlines.",
                "url": "https://newsapi.org",
            }
            for i in range(count)
        ]
        return json.dumps({"note": "Demo mode", "articles": articles})
