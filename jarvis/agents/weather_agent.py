"""Weather agent — current conditions and forecasts via OpenWeatherMap."""

import json
from typing import Any

import requests

from jarvis.agents.base_agent import BaseAgent


class WeatherAgent(BaseAgent):
    name = "weather"
    description = "Fetches current weather and forecasts from OpenWeatherMap."

    def __init__(self, agent_cfg: dict, global_cfg: dict) -> None:
        super().__init__(agent_cfg, global_cfg)
        self._api_key: str = agent_cfg.get("api_key", "")
        self._units: str = agent_cfg.get("units", "metric")
        self._base = "https://api.openweathermap.org/data/2.5"

    # ── Tool schemas ────────────────────────────────────────────────────────

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "get_current_weather",
                "description": (
                    "Returns the current weather conditions for a city "
                    "(temperature, humidity, wind, description)."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "city": {
                            "type": "string",
                            "description": "City name, e.g. 'Lahore'. Defaults to user's configured city.",
                        }
                    },
                    "required": [],
                },
            },
            {
                "name": "get_weather_forecast",
                "description": "Returns a 5-day / 3-hour weather forecast for a city.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string"},
                        "days": {
                            "type": "integer",
                            "description": "Number of days (1-5). Default 3.",
                        },
                    },
                    "required": [],
                },
            },
        ]

    def handle(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        city = tool_input.get("city") or self._city()
        if tool_name == "get_current_weather":
            return self._current(city)
        if tool_name == "get_weather_forecast":
            return self._forecast(city, int(tool_input.get("days", 3)))
        return f"Unknown tool: {tool_name}"

    # ── Implementation ──────────────────────────────────────────────────────

    def _current(self, city: str) -> str:
        if not self._api_key or self._api_key.startswith("YOUR_"):
            return self._demo_current(city)
        try:
            r = requests.get(
                f"{self._base}/weather",
                params={"q": city, "appid": self._api_key, "units": self._units},
                timeout=8,
            )
            r.raise_for_status()
            d = r.json()
            unit_sym = "°C" if self._units == "metric" else "°F"
            return json.dumps(
                {
                    "city": d["name"],
                    "country": d["sys"]["country"],
                    "temp": f"{d['main']['temp']}{unit_sym}",
                    "feels_like": f"{d['main']['feels_like']}{unit_sym}",
                    "humidity": f"{d['main']['humidity']}%",
                    "wind_speed": f"{d['wind']['speed']} m/s",
                    "description": d["weather"][0]["description"].capitalize(),
                    "visibility_km": d.get("visibility", 0) / 1000,
                }
            )
        except Exception as exc:
            return f"Weather API error: {exc}"

    def _forecast(self, city: str, days: int) -> str:
        if not self._api_key or self._api_key.startswith("YOUR_"):
            return self._demo_forecast(city, days)
        try:
            r = requests.get(
                f"{self._base}/forecast",
                params={
                    "q": city,
                    "appid": self._api_key,
                    "units": self._units,
                    "cnt": days * 8,  # 8 slots/day
                },
                timeout=8,
            )
            r.raise_for_status()
            d = r.json()
            unit_sym = "°C" if self._units == "metric" else "°F"
            summary = []
            seen_dates: set[str] = set()
            for item in d["list"]:
                date = item["dt_txt"].split(" ")[0]
                if date not in seen_dates and len(seen_dates) < days:
                    seen_dates.add(date)
                    summary.append(
                        {
                            "date": date,
                            "high": f"{item['main']['temp_max']}{unit_sym}",
                            "low": f"{item['main']['temp_min']}{unit_sym}",
                            "description": item["weather"][0]["description"].capitalize(),
                            "humidity": f"{item['main']['humidity']}%",
                        }
                    )
            return json.dumps({"city": city, "forecast": summary})
        except Exception as exc:
            return f"Forecast API error: {exc}"

    # ── Demo/fallback data (used when no API key is configured) ─────────────

    def _demo_current(self, city: str) -> str:
        return json.dumps(
            {
                "city": city,
                "temp": "29°C",
                "feels_like": "33°C",
                "humidity": "80%",
                "wind_speed": "5.1 m/s",
                "description": "Partly cloudy with sea breeze",
                "visibility_km": 9.0,
            }
        )

    def _demo_forecast(self, city: str, days: int) -> str:
        forecast = [
            {"date": f"Day {i+1}", "high": "31°C", "low": "24°C", "description": "Sunny intervals", "humidity": "78%"}
            for i in range(days)
        ]
        return json.dumps({"city": city, "forecast": forecast})
