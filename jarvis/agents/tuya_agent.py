"""Tuya Smart Home agent — connects to Tuya Open Platform cloud API.

Add to config.local.yaml:
  tuya:
    enabled: true
    access_id: "YOUR_TUYA_ACCESS_ID"
    access_secret: "YOUR_TUYA_ACCESS_SECRET"
    # Region endpoint — pick closest:
    #   openapi.tuyaus.com  (Americas)
    #   openapi.tuyaeu.com  (Europe/Africa)  ← default for Kenya
    #   openapi.tuyacn.com  (China)
    #   openapi.tuyain.com  (India)
    base_url: "https://openapi.tuyaeu.com"
    home_id: ""   # optional: filter by home ID

Get credentials:
  1. Go to https://platform.tuya.com → Cloud → Create project
  2. Under "Linked Devices" bind your Tuya Smart app account
  3. Note the Access ID and Access Secret
"""

import hashlib
import hmac
import json
import time
from typing import Any

import requests

from jarvis.agents.base_agent import BaseAgent

_TOKEN_CACHE: dict[str, Any] = {}


class TuyaAgent(BaseAgent):
    name = "tuya"
    description = "Controls Tuya/Smart Life smart home devices via the Tuya Open Platform cloud API."

    def __init__(self, cfg: dict, global_cfg: dict) -> None:
        super().__init__(cfg, global_cfg)
        self._id     = cfg.get("access_id", "")
        self._secret = cfg.get("access_secret", "")
        self._base   = cfg.get("base_url", "https://openapi.tuyaeu.com").rstrip("/")
        self._home   = cfg.get("home_id", "")

    # ── Tools ─────────────────────────────────────────────────────────────────

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "tuya_list_devices",
                "description": "Lists all Tuya smart home devices with their current status (on/off, brightness, etc.).",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "tuya_control_device",
                "description": (
                    "Controls a Tuya smart device — turn on/off, set brightness, colour temperature, etc. "
                    "Use the device_id from tuya_list_devices."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "device_id": {"type": "string", "description": "Tuya device ID"},
                        "device_name": {"type": "string", "description": "Human name (for display only)"},
                        "command": {
                            "type": "string",
                            "enum": ["turn_on", "turn_off", "toggle", "set_brightness", "set_colour_temp", "set_scene"],
                            "description": "Action to perform",
                        },
                        "value": {
                            "type": "integer",
                            "description": "Numeric value for commands that need it: brightness 10-1000, colour_temp 0-1000",
                        },
                    },
                    "required": ["device_id", "command"],
                },
            },
            {
                "name": "tuya_get_device_status",
                "description": "Gets the real-time status of a specific Tuya device.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "device_id": {"type": "string", "description": "Tuya device ID"},
                    },
                    "required": ["device_id"],
                },
            },
            {
                "name": "tuya_list_homes",
                "description": "Lists all homes/spaces registered in the Tuya account.",
                "input_schema": {"type": "object", "properties": {}},
            },
        ]

    # ── Dispatch ──────────────────────────────────────────────────────────────

    def handle(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if not self._id or not self._secret:
            return json.dumps({"error": "Tuya not configured. Add access_id and access_secret to config.local.yaml under tuya:"})
        if tool_name == "tuya_list_devices":     return self._list_devices()
        if tool_name == "tuya_control_device":   return self._control(tool_input)
        if tool_name == "tuya_get_device_status": return self._device_status(tool_input["device_id"])
        if tool_name == "tuya_list_homes":       return self._list_homes()
        return f"Unknown tool: {tool_name}"

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _sign(self, method: str, path: str, body: str = "", token: str = "") -> dict:
        t = str(int(time.time() * 1000))
        content_hash = hashlib.sha256(body.encode()).hexdigest()
        str_to_sign = "\n".join([method, content_hash, "", path])
        sign_str = self._id + token + t + str_to_sign
        sign = hmac.new(self._secret.encode(), sign_str.encode(), hashlib.sha256).hexdigest().upper()
        return {
            "client_id":   self._id,
            "t":           t,
            "sign_method": "HMAC-SHA256",
            "sign":        sign,
            "access_token": token,
        }

    def _get_token(self) -> str:
        global _TOKEN_CACHE
        now = time.time()
        if _TOKEN_CACHE.get("token") and now < _TOKEN_CACHE.get("expires", 0) - 60:
            return _TOKEN_CACHE["token"]

        path = "/v1.0/token?grant_type=1"
        headers = self._sign("GET", path)
        r = requests.get(self._base + path, headers=headers, timeout=8)
        d = r.json()
        if not d.get("success"):
            raise RuntimeError(f"Tuya auth failed: {d.get('msg', d)}")
        result = d["result"]
        _TOKEN_CACHE = {
            "token":   result["access_token"],
            "expires": now + result.get("expire_time", 7200),
        }
        return _TOKEN_CACHE["token"]

    def _get(self, path: str) -> dict:
        tok = self._get_token()
        headers = self._sign("GET", path, token=tok)
        r = requests.get(self._base + path, headers=headers, timeout=8)
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        tok = self._get_token()
        body_str = json.dumps(body)
        headers = self._sign("POST", path, body=body_str, token=tok)
        headers["Content-Type"] = "application/json"
        r = requests.post(self._base + path, headers=headers, data=body_str, timeout=8)
        return r.json()

    # ── Implementations ───────────────────────────────────────────────────────

    def _list_homes(self) -> str:
        try:
            d = self._get("/v1.0/homes?page_no=1&page_size=50")
            if not d.get("success"):
                return json.dumps({"error": d.get("msg", "Failed to list homes")})
            homes = d.get("result", {}).get("list", d.get("result", []))
            if isinstance(homes, dict):
                homes = homes.get("list", [])
            return json.dumps({"homes": [{"id": h.get("home_id") or h.get("id"), "name": h.get("name")} for h in homes]})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _list_devices(self) -> str:
        try:
            if self._home:
                path = f"/v1.0/homes/{self._home}/devices?page_no=1&page_size=100"
            else:
                path = f"/v1.0/iot-01/associated-users/devices?page_no=1&page_size=100"
            d = self._get(path)
            if not d.get("success"):
                # fallback path
                d = self._get("/v1.0/devices?page_no=1&page_size=100")
            if not d.get("success"):
                return json.dumps({"error": d.get("msg", "Failed to list devices")})
            raw = d.get("result", {})
            items = raw.get("list", raw) if isinstance(raw, dict) else raw
            devices = []
            for dev in items:
                status_map = {s["code"]: s["value"] for s in dev.get("status", [])}
                devices.append({
                    "id":       dev.get("id"),
                    "name":     dev.get("name", "Unknown"),
                    "category": dev.get("category", ""),
                    "online":   dev.get("online", False),
                    "on":       status_map.get("switch_led") or status_map.get("switch", False),
                    "status":   status_map,
                })
            online = sum(1 for d in devices if d["online"])
            return json.dumps({"devices": devices, "total": len(devices), "online": online})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _device_status(self, device_id: str) -> str:
        try:
            d = self._get(f"/v1.0/devices/{device_id}/status")
            if not d.get("success"):
                return json.dumps({"error": d.get("msg", "Failed")})
            status = {s["code"]: s["value"] for s in d.get("result", [])}
            return json.dumps({"device_id": device_id, "status": status})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _control(self, inp: dict) -> str:
        device_id   = inp["device_id"]
        command     = inp["command"]
        value       = inp.get("value")
        device_name = inp.get("device_name", device_id)

        # Map command → Tuya DP code
        try:
            current_on = False
            if command == "toggle":
                d = self._get(f"/v1.0/devices/{device_id}/status")
                if d.get("success"):
                    for s in d.get("result", []):
                        if s["code"] in ("switch_led", "switch"):
                            current_on = s["value"]
                            break
                command = "turn_off" if current_on else "turn_on"

            if command == "turn_on":
                commands = [{"code": "switch_led", "value": True}, {"code": "switch", "value": True}]
            elif command == "turn_off":
                commands = [{"code": "switch_led", "value": False}, {"code": "switch", "value": False}]
            elif command == "set_brightness":
                commands = [{"code": "bright_value_v2", "value": int(value or 500)},
                            {"code": "bright_value",    "value": int(value or 500)}]
            elif command == "set_colour_temp":
                commands = [{"code": "temp_value_v2", "value": int(value or 500)},
                            {"code": "temp_value",    "value": int(value or 500)}]
            elif command == "set_scene":
                commands = [{"code": "work_mode", "value": "scene"}]
            else:
                return json.dumps({"error": f"Unknown command: {command}"})

            # Try each command; some devices may not support all codes
            d = self._post(f"/v1.0/devices/{device_id}/commands", {"commands": commands[:1]})
            if not d.get("success"):
                # Try alternate code
                d = self._post(f"/v1.0/devices/{device_id}/commands", {"commands": commands[1:] if len(commands) > 1 else commands})

            return json.dumps({
                "device_id":   device_id,
                "device_name": device_name,
                "command":     command,
                "success":     d.get("success", False),
                "message":     "Done" if d.get("success") else d.get("msg", "Failed"),
            })
        except Exception as e:
            return json.dumps({"error": str(e)})
