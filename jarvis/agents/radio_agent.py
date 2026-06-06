"""Radio agent — free internet radio via Radio Browser API (no key needed)."""

import json
from typing import Any

import requests

from jarvis.agents.base_agent import BaseAgent

_HEADERS = {"User-Agent": "SahilAI/1.0"}
_BASE = "https://de1.api.radio-browser.info/json"


class RadioAgent(BaseAgent):
    name = "radio"
    description = "Searches and plays internet radio stations via the Radio Browser API (free, no key)."

    def __init__(self, agent_cfg: dict, global_cfg: dict) -> None:
        super().__init__(agent_cfg, global_cfg)

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "list_radio_stations",
                "description": (
                    "Search for internet radio stations by country and optional genre/tag. "
                    "Returns a list of stations with id, name, stream URL, genre, and bitrate."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "country": {
                            "type": "string",
                            "description": "Full country name, e.g. 'Kenya', 'United Kingdom', 'Germany'.",
                        },
                        "genre": {
                            "type": "string",
                            "description": "Optional genre/tag filter, e.g. 'jazz', 'news', 'pop'.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of stations to return. Default 15.",
                        },
                    },
                    "required": ["country"],
                },
            },
            {
                "name": "play_radio",
                "description": (
                    "Get the stream URL and metadata for a radio station so Sahil can play it. "
                    "Provide either the station name or its Radio Browser UUID."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "station_name": {
                            "type": "string",
                            "description": "Name of the radio station to play.",
                        },
                        "station_id": {
                            "type": "string",
                            "description": "Optional Radio Browser UUID for exact lookup.",
                        },
                    },
                    "required": ["station_name"],
                },
            },
        ]

    def handle(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name == "list_radio_stations":
            return self._list_stations(
                country=tool_input.get("country", ""),
                genre=tool_input.get("genre"),
                limit=int(tool_input.get("limit", 15)),
            )
        if tool_name == "play_radio":
            return self._play_radio(
                station_name=tool_input.get("station_name", ""),
                station_id=tool_input.get("station_id"),
            )
        return f"Unknown tool: {tool_name}"

    # ── Private helpers ──────────────────────────────────────────────────────

    def _list_stations(self, country: str, genre: str | None, limit: int) -> str:
        params: dict[str, Any] = {
            "country": country,
            "limit": limit,
            "order": "clickcount",
            "reverse": "true",
            "hidebroken": "true",
        }
        if genre:
            params["tag"] = genre
        try:
            r = requests.get(
                f"{_BASE}/stations/search",
                params=params,
                headers=_HEADERS,
                timeout=8,
            )
            r.raise_for_status()
            stations = r.json()
            result = [
                {
                    "id": s.get("stationuuid", ""),
                    "name": s.get("name", ""),
                    "url": s.get("url_resolved") or s.get("url", ""),
                    "genre": s.get("tags", ""),
                    "bitrate": s.get("bitrate", 0),
                    "country": s.get("country", ""),
                }
                for s in stations
                if s.get("url_resolved") or s.get("url")
            ]
            return json.dumps({"stations": result, "count": len(result)})
        except Exception as exc:
            return json.dumps({"error": f"Radio Browser API error: {exc}", "stations": []})

    def _play_radio(self, station_name: str, station_id: str | None) -> str:
        try:
            station = None

            # Prefer exact UUID lookup if provided
            if station_id:
                r = requests.get(
                    f"{_BASE}/stations/byuuid/{station_id}",
                    headers=_HEADERS,
                    timeout=8,
                )
                r.raise_for_status()
                data = r.json()
                if data:
                    station = data[0] if isinstance(data, list) else data

            # Fall back to name search
            if not station:
                r = requests.get(
                    f"{_BASE}/stations/search",
                    params={"name": station_name, "limit": 1, "hidebroken": "true"},
                    headers=_HEADERS,
                    timeout=8,
                )
                r.raise_for_status()
                results = r.json()
                if results:
                    station = results[0]

            if not station:
                return json.dumps({"error": f"Station '{station_name}' not found."})

            url = station.get("url_resolved") or station.get("url", "")
            return json.dumps({
                "action": "play_radio",
                "name": station.get("name", station_name),
                "url": url,
                "favicon": station.get("favicon", ""),
                "country": station.get("country", ""),
                "genre": station.get("tags", ""),
            })
        except Exception as exc:
            return json.dumps({"error": f"Radio Browser API error: {exc}"})
