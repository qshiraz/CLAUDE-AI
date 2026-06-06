"""Hik-Connect / Guard Vision credential-based agent.

Logs in with Guard Vision username + password — no developer account needed.
Add credentials to config.local.yaml under hik_connect (never commit them).
"""

import hashlib
import json
import time
import uuid
from datetime import datetime
from typing import Any

import requests

from jarvis.agents.base_agent import BaseAgent

_SESSION: dict = {}   # {session_id, expiry, user_id}


class HikConnectAgent(BaseAgent):
    name = "hik_connect"
    description = (
        "Connects to Guard Vision / Hik-Connect cloud cameras using your "
        "account username and password. No developer API key needed."
    )

    _LOGIN_URL  = "https://api.hik-connect.com/v3/users/login"
    _CAMERA_URL = "https://api.hik-connect.com/v3/userdevices/v1/devices/pagelist"
    _SNAP_URL   = "https://api.hik-connect.com/v3/snapshots/cameras/{index_code}/files"
    _STREAM_URL = "https://api.hik-connect.com/v3/video/cameras/{index_code}/protocols"

    _HEADERS = {
        "clientType":    "55",
        "lang":          "en-US",
        "Content-Type":  "application/x-www-form-urlencoded;charset=UTF-8",
        "featureCode":   "deadbeef12345678",   # static device fingerprint
    }

    def __init__(self, cfg: dict, global_cfg: dict) -> None:
        super().__init__(cfg, global_cfg)
        self._username: str = cfg.get("username", "")
        self._password: str = cfg.get("password", "")
        self._session_id: str = ""
        self._session_expiry: float = 0.0
        self._cam_cache: list[dict] = []
        self._cache_ts: float = 0.0

    # ── Tools ────────────────────────────────────────────────────────────────

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "hc_list_cameras",
                "description": "Lists all cameras in your Guard Vision / Hik-Connect account.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "hc_analyze_camera",
                "description": (
                    "Fetches a live snapshot from a Guard Vision camera "
                    "and uses AI vision to describe what is happening."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "camera_name": {
                            "type": "string",
                            "description": "Camera name as shown in Guard Vision app",
                        },
                        "focus": {
                            "type": "string",
                            "enum": ["security", "people", "vehicles", "general"],
                        },
                    },
                    "required": ["camera_name"],
                },
            },
            {
                "name": "hc_get_stream",
                "description": "Returns a live stream URL for a Guard Vision camera (open in VLC).",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "camera_name": {"type": "string"},
                    },
                    "required": ["camera_name"],
                },
            },
        ]

    # ── Dispatch ─────────────────────────────────────────────────────────────

    def handle(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name == "hc_list_cameras":  return self._list_cameras()
        if tool_name == "hc_analyze_camera": return self._analyze(tool_input)
        if tool_name == "hc_get_stream":    return self._get_stream(tool_input)
        return f"Unknown tool: {tool_name}"

    # ── Auth ─────────────────────────────────────────────────────────────────

    def _has_creds(self) -> bool:
        return bool(self._username and self._password)

    def _login(self) -> str:
        """Log in and return session ID. Reuses valid session."""
        if self._session_id and time.time() < self._session_expiry:
            return self._session_id

        md5_pw = hashlib.md5(self._password.encode("utf-8")).hexdigest()

        resp = requests.post(
            self._LOGIN_URL,
            data={
                "account":     self._username,
                "password":    md5_pw,
                "featureCode": "deadbeef12345678",
                "clientType":  "55",
                "lang":        "en-US",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        code = str(data.get("code", ""))
        if code != "200":
            msg = data.get("msg", "Login failed")
            raise RuntimeError(f"Hik-Connect login error {code}: {msg}")

        session = data.get("loginSession") or data.get("mloginSession") or {}
        sid = session.get("sessionId", "")
        if not sid:
            raise RuntimeError("No session ID returned — check username/password")

        self._session_id = sid
        self._session_expiry = time.time() + 3600   # sessions last ~1 hour
        return sid

    def _api_get(self, url: str, params: dict | None = None) -> dict:
        sid = self._login()
        headers = {**self._HEADERS, "sessionId": sid}
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _api_post(self, url: str, data: dict | None = None) -> dict | bytes:
        sid = self._login()
        headers = {**self._HEADERS, "sessionId": sid}
        resp = requests.post(url, headers=headers, json=data, timeout=15)
        resp.raise_for_status()
        ct = resp.headers.get("Content-Type", "")
        return resp.content if "image" in ct or "octet" in ct else resp.json()

    # ── Camera list ───────────────────────────────────────────────────────────

    def _get_cameras(self) -> list[dict]:
        if self._cam_cache and time.time() - self._cache_ts < 300:
            return self._cam_cache
        data = self._api_get(self._CAMERA_URL, {"pageStart": 0, "pageSize": 100})
        devices = (data.get("cameraInfoList") or
                   data.get("deviceInfos") or
                   data.get("data") or [])
        self._cam_cache = devices if isinstance(devices, list) else []
        self._cache_ts = time.time()
        return self._cam_cache

    def _find_cam(self, name: str) -> dict | None:
        low = name.lower()
        for c in self._cam_cache:
            cam_name = (c.get("cameraName") or c.get("deviceName") or "").lower()
            if cam_name == low or low in cam_name:
                return c
        return None

    # ── Implementations ───────────────────────────────────────────────────────

    def _list_cameras(self) -> str:
        if not self._has_creds():
            return json.dumps({
                "error": "Guard Vision credentials not set.",
                "fix": (
                    "Add to config.local.yaml:\n"
                    "hik_connect:\n"
                    "  username: 'your_guardvision_email_or_phone'\n"
                    "  password: 'your_guardvision_password'"
                ),
            })
        try:
            cameras = self._get_cameras()
            result = []
            for c in cameras:
                result.append({
                    "name":   c.get("cameraName") or c.get("deviceName") or "Unknown",
                    "serial": c.get("deviceSerial", ""),
                    "model":  c.get("deviceType", ""),
                    "status": "ONLINE" if str(c.get("status", "0")) == "1" else "OFFLINE",
                })
            return json.dumps({"cameras": result, "total": len(result)})
        except Exception as exc:
            return json.dumps({"error": str(exc),
                               "hint": "Check username/password in config.local.yaml"})

    def _analyze(self, inp: dict) -> str:
        if not self._has_creds():
            return self._list_cameras()
        try:
            self._get_cameras()
            cam = self._find_cam(inp["camera_name"])
            if not cam:
                names = [c.get("cameraName") or c.get("deviceName") for c in self._cam_cache]
                return json.dumps({"error": f"Camera not found. Available: {names}"})

            index_code = cam.get("cameraIndexCode") or cam.get("deviceSerial", "")
            snap_result = self._api_get(
                self._SNAP_URL.format(index_code=index_code),
                {"channelNo": 1, "qualityLevel": 1, "transType": 1}
            )
            img_url = (snap_result.get("picUrl") or
                       (snap_result.get("data") or {}).get("url") or "")

            if not img_url:
                return json.dumps({"error": "Could not get snapshot URL from camera."})

            img_resp = requests.get(img_url, timeout=15)
            img_resp.raise_for_status()
            import base64
            b64 = base64.b64encode(img_resp.content).decode()

            focus = inp.get("focus", "security")
            prompts = {
                "security": "Identify any security concerns: intruders, suspicious vehicles, unusual activity.",
                "people":   "Describe all people visible — clothing, activity, direction of movement.",
                "vehicles": "Identify vehicles — type, colour, any visible licence plate.",
                "general":  "Describe the full scene in detail.",
            }
            return json.dumps({
                "camera":    cam.get("cameraName", inp["camera_name"]),
                "timestamp": datetime.now().isoformat(),
                "image_base64": b64,
                "analysis_instruction": (
                    f"Analyse this live security camera snapshot from '{cam.get('cameraName','')}'. "
                    f"{prompts.get(focus, prompts['general'])} Be specific and security-focused."
                ),
            })
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    def _get_stream(self, inp: dict) -> str:
        if not self._has_creds():
            return self._list_cameras()
        try:
            self._get_cameras()
            cam = self._find_cam(inp["camera_name"])
            if not cam:
                return json.dumps({"error": "Camera not found."})
            index_code = cam.get("cameraIndexCode") or cam.get("deviceSerial", "")
            data = self._api_get(
                self._STREAM_URL.format(index_code=index_code),
                {"streamType": 0, "protocol": "hls", "transType": 1}
            )
            url = (data.get("url") or
                   (data.get("data") or {}).get("url") or "")
            return json.dumps({
                "camera":       cam.get("cameraName", ""),
                "stream_url":   url,
                "instructions": "Open in VLC: Media → Open Network Stream → paste URL",
            })
        except Exception as exc:
            return json.dumps({"error": str(exc)})
