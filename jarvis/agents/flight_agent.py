"""Flight agent — nearby flight patterns via AviationStack / OpenSky Network."""

import json
import math
from typing import Any

import requests

from jarvis.agents.base_agent import BaseAgent


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class FlightAgent(BaseAgent):
    name = "flights"
    description = "Tracks flights over or near the user's area using AviationStack or OpenSky."

    def __init__(self, agent_cfg: dict, global_cfg: dict) -> None:
        super().__init__(agent_cfg, global_cfg)
        self._api_key: str = agent_cfg.get("api_key", "")
        self._radius: float = float(agent_cfg.get("radius_km", 150))

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "get_nearby_flights",
                "description": (
                    "Returns flights currently in the air within the configured radius of the user's location, "
                    "including aircraft, altitude, speed, and origin/destination."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "radius_km": {
                            "type": "number",
                            "description": "Search radius in km. Defaults to configured value.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max number of flights to return (default 10).",
                        },
                    },
                    "required": [],
                },
            },
            {
                "name": "get_airport_departures",
                "description": "Returns upcoming departures from the user's nearest airport.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "iata": {
                            "type": "string",
                            "description": "Airport IATA code (e.g. 'LHE'). Defaults to configured airport.",
                        },
                        "limit": {"type": "integer"},
                    },
                    "required": [],
                },
            },
        ]

    def handle(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name == "get_nearby_flights":
            return self._nearby(
                float(tool_input.get("radius_km", self._radius)),
                int(tool_input.get("limit", 10)),
            )
        if tool_name == "get_airport_departures":
            iata = tool_input.get("iata") or self.location.get("iata_airport", "LHE")
            return self._departures(iata, int(tool_input.get("limit", 8)))
        return f"Unknown tool: {tool_name}"

    # ── OpenSky (free, no key needed) ───────────────────────────────────────

    def _nearby(self, radius: float, limit: int) -> str:
        lat, lon = self._lat(), self._lon()
        deg = radius / 111.0
        params = {
            "lamin": lat - deg,
            "lomin": lon - deg,
            "lamax": lat + deg,
            "lomax": lon + deg,
        }
        try:
            r = requests.get(
                "https://opensky-network.org/api/states/all",
                params=params,
                timeout=10,
            )
            r.raise_for_status()
            states = r.json().get("states") or []
        except Exception as exc:
            return self._demo_flights(limit)

        flights = []
        for s in states:
            if s is None:
                continue
            icao = s[0] or ""
            callsign = (s[1] or "").strip()
            origin_country = s[2] or ""
            lon_f = s[5]
            lat_f = s[6]
            alt_m = s[7] or s[13] or 0
            velocity = s[9] or 0
            track = s[10] or 0
            on_ground = s[8]

            if lat_f is None or lon_f is None or on_ground:
                continue
            dist = _haversine_km(lat, lon, lat_f, lon_f)
            if dist > radius:
                continue

            flights.append(
                {
                    "callsign": callsign or icao,
                    "origin_country": origin_country,
                    "altitude_m": round(alt_m),
                    "speed_kmh": round(velocity * 3.6),
                    "heading_deg": round(track),
                    "distance_km": round(dist, 1),
                    "lat": round(lat_f, 3),
                    "lon": round(lon_f, 3),
                }
            )

        flights.sort(key=lambda x: x["distance_km"])
        return json.dumps({"radius_km": radius, "count": len(flights), "flights": flights[:limit]})

    def _departures(self, iata: str, limit: int) -> str:
        if not self._api_key or self._api_key.startswith("YOUR_"):
            return self._demo_departures(iata, limit)
        try:
            r = requests.get(
                "http://api.aviationstack.com/v1/flights",
                params={
                    "access_key": self._api_key,
                    "dep_iata": iata,
                    "flight_status": "scheduled",
                    "limit": limit,
                },
                timeout=10,
            )
            r.raise_for_status()
            data = r.json().get("data", [])
            flights = [
                {
                    "flight": d.get("flight", {}).get("iata", ""),
                    "airline": d.get("airline", {}).get("name", ""),
                    "destination": d.get("arrival", {}).get("iata", ""),
                    "scheduled": d.get("departure", {}).get("scheduled", ""),
                    "status": d.get("flight_status", ""),
                }
                for d in data
            ]
            return json.dumps({"airport": iata, "departures": flights})
        except Exception as exc:
            return f"Flight API error: {exc}"

    def _demo_flights(self, limit: int) -> str:
        flights = [
            {
                "callsign": f"PK{100+i}",
                "origin_country": "Pakistan",
                "altitude_m": 8000 + i * 500,
                "speed_kmh": 820,
                "heading_deg": 45 * i % 360,
                "distance_km": 20 + i * 15,
            }
            for i in range(min(limit, 4))
        ]
        return json.dumps({"note": "Demo mode — live data from OpenSky Network (no key needed)", "flights": flights})

    def _demo_departures(self, iata: str, limit: int) -> str:
        deps = [
            {
                "flight": f"PK{200+i}",
                "airline": "Pakistan International Airlines",
                "destination": ["DXB", "ISB", "KHI", "LHR"][i % 4],
                "scheduled": f"2026-06-05T{10+i:02d}:00:00+05:00",
                "status": "scheduled",
            }
            for i in range(min(limit, 4))
        ]
        return json.dumps({"note": "Demo mode — configure AviationStack key for live data", "airport": iata, "departures": deps})
