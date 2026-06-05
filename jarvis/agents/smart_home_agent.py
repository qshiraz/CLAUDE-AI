"""Smart home agent — control devices via Home Assistant REST API."""

import json
from typing import Any

import requests

from jarvis.agents.base_agent import BaseAgent


class SmartHomeAgent(BaseAgent):
    name = "smart_home"
    description = "Controls and queries smart home devices through Home Assistant."

    def __init__(self, agent_cfg: dict, global_cfg: dict) -> None:
        super().__init__(agent_cfg, global_cfg)
        self._ha_url: str = agent_cfg.get("ha_url", "http://homeassistant.local:8123").rstrip("/")
        self._token: str = agent_cfg.get("ha_token", "")
        self._headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "list_devices",
                "description": "Lists all smart home devices and their current states.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "domain": {
                            "type": "string",
                            "description": "Optional filter by domain: 'light', 'switch', 'climate', 'sensor', etc.",
                        }
                    },
                    "required": [],
                },
            },
            {
                "name": "control_device",
                "description": "Turn a device on or off, or set a specific state.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "entity_id": {
                            "type": "string",
                            "description": "The Home Assistant entity_id, e.g. 'light.living_room'.",
                        },
                        "action": {
                            "type": "string",
                            "enum": ["turn_on", "turn_off", "toggle"],
                            "description": "Action to perform.",
                        },
                        "brightness": {
                            "type": "integer",
                            "description": "For lights: brightness 0-255.",
                        },
                        "temperature": {
                            "type": "number",
                            "description": "For climate devices: target temperature.",
                        },
                    },
                    "required": ["entity_id", "action"],
                },
            },
            {
                "name": "get_device_state",
                "description": "Gets the current state of a specific device.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "entity_id": {
                            "type": "string",
                            "description": "The entity_id to query.",
                        }
                    },
                    "required": ["entity_id"],
                },
            },
        ]

    def handle(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name == "list_devices":
            return self._list_devices(tool_input.get("domain", ""))
        if tool_name == "control_device":
            return self._control(
                tool_input["entity_id"],
                tool_input["action"],
                tool_input.get("brightness"),
                tool_input.get("temperature"),
            )
        if tool_name == "get_device_state":
            return self._get_state(tool_input["entity_id"])
        return f"Unknown tool: {tool_name}"

    # ── HA REST calls ───────────────────────────────────────────────────────

    def _demo_mode(self) -> bool:
        return not self._token or self._token.startswith("YOUR_")

    def _list_devices(self, domain: str) -> str:
        if self._demo_mode():
            return self._demo_devices(domain)
        try:
            r = requests.get(f"{self._ha_url}/api/states", headers=self._headers, timeout=8)
            r.raise_for_status()
            states = r.json()
            if domain:
                states = [s for s in states if s["entity_id"].startswith(f"{domain}.")]
            devices = [
                {
                    "entity_id": s["entity_id"],
                    "state": s["state"],
                    "friendly_name": s["attributes"].get("friendly_name", ""),
                }
                for s in states[:30]
            ]
            return json.dumps({"count": len(devices), "devices": devices})
        except Exception as exc:
            return f"Smart home error: {exc}"

    def _control(
        self, entity_id: str, action: str, brightness: int | None, temperature: float | None
    ) -> str:
        if self._demo_mode():
            return json.dumps(
                {"note": "Demo mode", "entity_id": entity_id, "action": action, "result": "simulated"}
            )
        try:
            domain = entity_id.split(".")[0]
            service = action
            data: dict[str, Any] = {"entity_id": entity_id}
            if brightness is not None:
                data["brightness"] = brightness
            if temperature is not None:
                data["temperature"] = temperature
            r = requests.post(
                f"{self._ha_url}/api/services/{domain}/{service}",
                headers=self._headers,
                json=data,
                timeout=8,
            )
            r.raise_for_status()
            return json.dumps({"success": True, "entity_id": entity_id, "action": action})
        except Exception as exc:
            return f"Control error: {exc}"

    def _get_state(self, entity_id: str) -> str:
        if self._demo_mode():
            return json.dumps(
                {"note": "Demo mode", "entity_id": entity_id, "state": "on", "attributes": {}}
            )
        try:
            r = requests.get(
                f"{self._ha_url}/api/states/{entity_id}",
                headers=self._headers,
                timeout=8,
            )
            r.raise_for_status()
            d = r.json()
            return json.dumps(
                {"entity_id": d["entity_id"], "state": d["state"], "attributes": d["attributes"]}
            )
        except Exception as exc:
            return f"State query error: {exc}"

    def _demo_devices(self, domain: str) -> str:
        devices = [
            {"entity_id": "light.living_room", "state": "on", "friendly_name": "Living Room Light"},
            {"entity_id": "light.bedroom", "state": "off", "friendly_name": "Bedroom Light"},
            {"entity_id": "switch.fan", "state": "on", "friendly_name": "Ceiling Fan"},
            {"entity_id": "climate.ac", "state": "cool", "friendly_name": "Air Conditioner"},
            {"entity_id": "sensor.temp", "state": "24°C", "friendly_name": "Room Temperature"},
        ]
        if domain:
            devices = [d for d in devices if d["entity_id"].startswith(f"{domain}.")]
        return json.dumps(
            {"note": "Demo mode — configure Home Assistant in config.local.yaml", "devices": devices}
        )
