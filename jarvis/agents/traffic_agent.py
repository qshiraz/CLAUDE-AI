"""Traffic agent — live traffic conditions and ride estimates via TomTom."""

import json
from typing import Any

import requests

from jarvis.agents.base_agent import BaseAgent


class TrafficAgent(BaseAgent):
    name = "traffic"
    description = "Fetches live traffic conditions and route travel times via TomTom."

    def __init__(self, agent_cfg: dict, global_cfg: dict) -> None:
        super().__init__(agent_cfg, global_cfg)
        self._api_key: str = agent_cfg.get("api_key", "")
        self._radius: float = float(agent_cfg.get("radius_km", 30))

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "get_traffic_conditions",
                "description": (
                    "Returns current traffic flow conditions around the user's location, "
                    "including congestion levels and incident reports."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "radius_km": {
                            "type": "number",
                            "description": "Radius to scan (default from config).",
                        }
                    },
                    "required": [],
                },
            },
            {
                "name": "get_route_time",
                "description": (
                    "Estimates travel time between two locations accounting for live traffic. "
                    "Returns duration, distance, and traffic delay."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "origin": {
                            "type": "string",
                            "description": "Origin address or 'lat,lon' string.",
                        },
                        "destination": {
                            "type": "string",
                            "description": "Destination address or 'lat,lon' string.",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["car", "motorcycle", "pedestrian"],
                            "description": "Travel mode. Default: car.",
                        },
                    },
                    "required": ["origin", "destination"],
                },
            },
            {
                "name": "get_ride_estimate",
                "description": (
                    "Estimates ride cost and time (like a taxi/ride-share fare estimate) "
                    "between two points based on distance and traffic."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "origin": {"type": "string"},
                        "destination": {"type": "string"},
                    },
                    "required": ["origin", "destination"],
                },
            },
        ]

    def handle(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name == "get_traffic_conditions":
            return self._traffic(float(tool_input.get("radius_km", self._radius)))
        if tool_name == "get_route_time":
            return self._route(
                tool_input["origin"],
                tool_input["destination"],
                tool_input.get("mode", "car"),
            )
        if tool_name == "get_ride_estimate":
            return self._ride(tool_input["origin"], tool_input["destination"])
        return f"Unknown tool: {tool_name}"

    # ── TomTom API calls ────────────────────────────────────────────────────

    def _demo_mode(self) -> bool:
        return not self._api_key or self._api_key.startswith("YOUR_")

    def _traffic(self, radius_km: float) -> str:
        if self._demo_mode():
            return json.dumps(
                {
                    "location": "Mombasa, Kenya",
                    "overall_congestion": "Moderate",
                    "incidents": [
                        {"type": "congestion", "road": "Nyali Bridge", "delay_min": 15},
                        {"type": "roadwork", "road": "Mombasa-Malindi Highway (B8)", "delay_min": 10},
                        {"type": "congestion", "road": "Likoni Ferry Approach", "delay_min": 20},
                    ],
                    "flow_summary": "Heavy congestion at Nyali Bridge and Likoni Ferry. Digo Road and Moi Avenue moving freely.",
                }
            )
        lat, lon = self._lat(), self._lon()
        zoom = 10
        try:
            r = requests.get(
                f"https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/{zoom}/json",
                params={"key": self._api_key, "point": f"{lat},{lon}"},
                timeout=8,
            )
            r.raise_for_status()
            d = r.json().get("flowSegmentData", {})
            return json.dumps(
                {
                    "current_speed_kmh": d.get("currentSpeed"),
                    "free_flow_speed_kmh": d.get("freeFlowSpeed"),
                    "confidence": d.get("confidence"),
                    "road_closure": d.get("roadClosure", False),
                }
            )
        except Exception as exc:
            return f"Traffic API error: {exc}"

    def _route(self, origin: str, destination: str, mode: str) -> str:
        if self._demo_mode():
            return json.dumps(
                {
                    "origin": origin,
                    "destination": destination,
                    "distance_km": 12.4,
                    "travel_time_min": 28,
                    "traffic_delay_min": 7,
                    "mode": mode,
                }
            )
        # Use TomTom Routing API
        # origin/destination can be "lat,lon" or we'd need geocoding
        try:
            r = requests.get(
                f"https://api.tomtom.com/routing/1/calculateRoute/{origin}:{destination}/json",
                params={"key": self._api_key, "travelMode": mode, "traffic": "true"},
                timeout=10,
            )
            r.raise_for_status()
            route = r.json()["routes"][0]["summary"]
            return json.dumps(
                {
                    "origin": origin,
                    "destination": destination,
                    "distance_km": round(route["lengthInMeters"] / 1000, 1),
                    "travel_time_min": round(route["travelTimeInSeconds"] / 60),
                    "traffic_delay_min": round(route.get("trafficDelayInSeconds", 0) / 60),
                    "mode": mode,
                }
            )
        except Exception as exc:
            return f"Routing error: {exc}"

    def _ride(self, origin: str, destination: str) -> str:
        # Use route data to estimate ride cost (PKR/km approximate)
        route_json = self._route(origin, destination, "car")
        try:
            route = json.loads(route_json)
            dist = route.get("distance_km", 10)
            time = route.get("travel_time_min", 20)
            base_fare = 200   # KES base flag-fall
            per_km = 90       # KES/km (Mombasa Little/Uber/Bolt approximate)
            per_min = 8       # KES/min
            est = round(base_fare + dist * per_km + time * per_min)
            return json.dumps(
                {
                    "origin": origin,
                    "destination": destination,
                    "distance_km": dist,
                    "estimated_time_min": time,
                    "estimated_fare_KES": f"KES {est:,}",
                    "note": "Approximate fare (Little/Uber/Bolt, Mombasa rates).",
                }
            )
        except Exception as exc:
            return f"Ride estimate error: {exc}"
