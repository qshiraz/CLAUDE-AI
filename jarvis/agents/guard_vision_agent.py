"""Guard Vision (Hik-Connect) cloud camera agent — remote site cameras via internet."""

import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime
from typing import Any

from jarvis.agents.base_agent import BaseAgent


class GuardVisionAgent(BaseAgent):
    """
    Integrates with Hikvision's Guard Vision (Hik-Connect) cloud platform.

    Credentials needed (add to config.local.yaml under guard_vision):
      app_key:    from open.hikvision.com developer account
      app_secret: from open.hikvision.com developer account

    If you don't have API credentials yet, the agent will guide you
    to obtain them. Your normal Guard Vision username/password alone
    is not enough — Hikvision requires a separate developer API key.
    """

    name = "guard_vision"
    description = (
        "Connects to remote Guard Vision (Hik-Connect) cameras over the internet. "
        "Lists cameras, fetches live snapshots, and analyses scenes using AI vision."
    )

    _BASE = "https://open.hikvision.com"

    def __init__(self, cfg: dict, global_cfg: dict) -> None:
        super().__init__(cfg, global_cfg)
        self._app_key: str = cfg.get("app_key", "")
        self._app_secret: str = cfg.get("app_secret", "")
        self._account: str = cfg.get("account", "")  # Guard Vision username
        # Camera index cache: {serial -> {name, deviceSerial, ...}}
        self._cam_cache: dict[str, dict] = {}
        self._cache_ts: float = 0

    # ── Tool schema ──────────────────────────────────────────────────────────

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "gv_list_cameras",
                "description": (
                    "Lists all cameras in the Guard Vision (Hik-Connect) cloud account "
                    "with their names, models, and online status."
                ),
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "gv_get_snapshot",
                "description": (
                    "Captures a live snapshot from a remote Guard Vision camera "
                    "and returns the image for AI vision analysis."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "camera_name": {
                            "type": "string",
                            "description": "Camera name as shown in the Guard Vision app",
                        },
                        "channel": {
                            "type": "integer",
                            "description": "Channel number (default 1)",
                        },
                    },
                    "required": ["camera_name"],
                },
            },
            {
                "name": "gv_analyze_scene",
                "description": (
                    "Fetches a live snapshot from a remote Guard Vision camera "
                    "and uses Claude vision to describe what is happening — "
                    "people, vehicles, activity, security concerns."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "camera_name": {"type": "string", "description": "Camera name"},
                        "focus": {
                            "type": "string",
                            "enum": ["security", "people", "vehicles", "general"],
                            "description": "What to focus on (default: security)",
                        },
                    },
                    "required": ["camera_name"],
                },
            },
            {
                "name": "gv_get_stream_url",
                "description": (
                    "Returns a live HLS/RTMP stream URL for a Guard Vision camera "
                    "that can be opened in VLC or embedded in a browser video player."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "camera_name": {"type": "string"},
                        "stream_type": {
                            "type": "string",
                            "enum": ["main", "sub"],
                            "description": "main = high quality, sub = low bandwidth",
                        },
                    },
                    "required": ["camera_name"],
                },
            },
            {
                "name": "gv_setup_guide",
                "description": (
                    "Returns step-by-step instructions for getting Guard Vision "
                    "API credentials (App Key and App Secret) from Hikvision's "
                    "developer platform so Jarvis can connect to the cameras."
                ),
                "input_schema": {"type": "object", "properties": {}},
            },
        ]

    # ── Dispatch ─────────────────────────────────────────────────────────────

    def handle(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name == "gv_list_cameras":   return self._list_cameras()
        if tool_name == "gv_get_snapshot":   return self._snapshot(tool_input)
        if tool_name == "gv_analyze_scene":  return self._analyze(tool_input)
        if tool_name == "gv_get_stream_url": return self._stream_url(tool_input)
        if tool_name == "gv_setup_guide":    return self._setup_guide()
        return f"Unknown tool: {tool_name}"

    # ── Credential check ──────────────────────────────────────────────────────

    def _has_creds(self) -> bool:
        return bool(self._app_key and self._app_secret)

    def _no_creds_msg(self) -> str:
        return json.dumps({
            "error": "Guard Vision API credentials not configured.",
            "fix": (
                "Jarvis needs an App Key and App Secret from Hikvision's developer platform. "
                "Call the gv_setup_guide tool for step-by-step instructions, "
                "then add the credentials to config.local.yaml under guard_vision."
            ),
        })

    # ── Hik-Connect OpenAPI auth ───────────────────────────────────────────────

    def _sign(self, path: str, method: str = "POST") -> dict:
        """Build the HMAC-SHA256 signed headers for Hik-Connect OpenAPI."""
        ts = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        string_to_sign = f"{method}\napplication/json\n{ts}\n{nonce}\n{path}"
        signature = hmac.new(
            self._app_secret.encode(),
            string_to_sign.encode(),
            hashlib.sha256,
        ).hexdigest()
        return {
            "Content-Type": "application/json",
            "x-ca-key": self._app_key,
            "x-ca-timestamp": ts,
            "x-ca-nonce": nonce,
            "x-ca-signature": signature,
            "x-ca-signature-headers": "x-ca-key,x-ca-timestamp,x-ca-nonce",
        }

    def _post(self, path: str, body: dict) -> dict:
        import requests  # type: ignore
        headers = self._sign(path, "POST")
        resp = requests.post(
            self._BASE + path,
            headers=headers,
            json=body,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def _get(self, path: str, params: dict | None = None) -> dict | bytes:
        import requests  # type: ignore
        headers = self._sign(path, "GET")
        resp = requests.get(
            self._BASE + path,
            headers=headers,
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        ct = resp.headers.get("Content-Type", "")
        return resp.content if "image" in ct else resp.json()

    # ── Camera list / cache ───────────────────────────────────────────────────

    def _refresh_cameras(self) -> list[dict]:
        """Fetch camera list from Hik-Connect API (cached for 5 min)."""
        if time.time() - self._cache_ts < 300 and self._cam_cache:
            return list(self._cam_cache.values())
        data = self._post("/api/resource/camera/search", {
            "pageNo": 1,
            "pageSize": 100,
        })
        cameras = (data.get("data") or {}).get("list") or []
        self._cam_cache = {c["cameraName"]: c for c in cameras}
        self._cache_ts = time.time()
        return cameras

    def _find_cam(self, name: str) -> dict | None:
        for cam in self._cam_cache.values():
            if cam.get("cameraName", "").lower() == name.lower():
                return cam
        return None

    # ── Implementations ───────────────────────────────────────────────────────

    def _list_cameras(self) -> str:
        if not self._has_creds():
            return self._no_creds_msg()
        try:
            cameras = self._refresh_cameras()
            result = []
            for c in cameras:
                result.append({
                    "name": c.get("cameraName", "Unknown"),
                    "serial": c.get("deviceSerial", ""),
                    "model": c.get("deviceType", ""),
                    "status": "ONLINE" if c.get("status") == "1" else "OFFLINE",
                    "location": c.get("treatyDeviceName", ""),
                })
            return json.dumps({
                "cameras": result,
                "total": len(result),
                "account": self._account,
            })
        except Exception as exc:
            return json.dumps({"error": str(exc), "hint": "Check your App Key/Secret in config.local.yaml"})

    def _snapshot(self, inp: dict) -> str:
        if not self._has_creds():
            return self._no_creds_msg()
        try:
            self._refresh_cameras()
            cam = self._find_cam(inp["camera_name"])
            if not cam:
                return json.dumps({"error": f"Camera '{inp['camera_name']}' not found. Use gv_list_cameras to see available cameras."})
            channel = int(inp.get("channel", 1))
            img_bytes = self._post("/api/video/capture", {
                "cameraIndexCode": cam["cameraIndexCode"],
                "channel": channel,
            })
            if isinstance(img_bytes, bytes) and len(img_bytes) > 100:
                b64 = base64.b64encode(img_bytes).decode()
                return json.dumps({
                    "camera": cam["cameraName"],
                    "timestamp": datetime.now().isoformat(),
                    "image_base64": b64,
                    "size_kb": round(len(img_bytes) / 1024, 1),
                })
            return json.dumps({"error": "Snapshot returned unexpected data", "raw": str(img_bytes)[:200]})
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    def _analyze(self, inp: dict) -> str:
        if not self._has_creds():
            return self._no_creds_msg()
        snap_result = json.loads(self._snapshot({"camera_name": inp["camera_name"]}))
        if "error" in snap_result:
            return json.dumps(snap_result)
        focus = inp.get("focus", "security")
        prompts = {
            "security": "Identify any security concerns: intruders, suspicious activity, unauthorized vehicles, access points.",
            "people": "Count and describe people present — approximate age, clothing, activity, direction of movement.",
            "vehicles": "Identify vehicles: type, colour, any visible licence plate characters.",
            "general": "Describe the scene in detail — time of day impression, activity level, notable objects or people.",
        }
        return json.dumps({
            "camera": snap_result["camera"],
            "timestamp": snap_result["timestamp"],
            "image_base64": snap_result["image_base64"],
            "analysis_instruction": (
                f"Analyse this live security camera image from '{snap_result['camera']}' "
                f"at the remote site. {prompts.get(focus, prompts['general'])} "
                "Be specific and concise. Note any anomalies or concerns."
            ),
        })

    def _stream_url(self, inp: dict) -> str:
        if not self._has_creds():
            return self._no_creds_msg()
        try:
            self._refresh_cameras()
            cam = self._find_cam(inp["camera_name"])
            if not cam:
                return json.dumps({"error": f"Camera '{inp['camera_name']}' not found."})
            stream_type = 0 if inp.get("stream_type", "main") == "main" else 1
            data = self._post("/api/video/urls/batch", {
                "list": [{"cameraIndexCode": cam["cameraIndexCode"], "streamType": stream_type}],
            })
            urls = (data.get("data") or [{}])[0]
            return json.dumps({
                "camera": cam["cameraName"],
                "hls_url": urls.get("hlsUrl", ""),
                "rtmp_url": urls.get("rtmpUrl", ""),
                "instructions": "Open the HLS URL in VLC: Media → Open Network Stream → paste URL",
            })
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    def _setup_guide(self) -> str:
        return json.dumps({
            "title": "Guard Vision API Setup — Step by Step",
            "steps": [
                {
                    "step": 1,
                    "action": "Open Hikvision Developer Platform",
                    "detail": "Go to: open.hikvision.com — this is separate from the Guard Vision app",
                },
                {
                    "step": 2,
                    "action": "Register a developer account",
                    "detail": "Click 'Register' and sign up using your email. This is free.",
                },
                {
                    "step": 3,
                    "action": "Create an Application",
                    "detail": "After logging in → 'My Apps' → 'Create App' → fill in app name 'Jarvis' → submit",
                },
                {
                    "step": 4,
                    "action": "Copy App Key and App Secret",
                    "detail": "Once approved (can take a few minutes), open your app to see App Key and App Secret",
                },
                {
                    "step": 5,
                    "action": "Add credentials to Jarvis",
                    "detail": (
                        "Open config.local.yaml and add:\n"
                        "guard_vision:\n"
                        "  app_key: 'paste_your_app_key_here'\n"
                        "  app_secret: 'paste_your_app_secret_here'\n"
                        "  account: 'your_guard_vision_username'"
                    ),
                },
                {
                    "step": 6,
                    "action": "Restart Jarvis",
                    "detail": "Stop the server (Ctrl+C) and run: python server.py",
                },
                {
                    "step": 7,
                    "action": "Test the connection",
                    "detail": "Ask Jarvis: 'List my Guard Vision cameras'",
                },
            ],
            "note": (
                "If open.hikvision.com is not available in your region, "
                "try the Hik-Connect Open Platform at: open.isapi.com"
            ),
        })
