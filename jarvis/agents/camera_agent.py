"""Hikvision DVR/NVR direct agent — connects via public IP using ISAPI.

No cloud account, no API key, no developer registration needed.
Works with any Hikvision or Hikvision-compatible DVR/NVR with a public IP.

Add to config.local.yaml:
  camera:
    dvr_ip: "41.84.157.45"
    dvr_username: "admin"
    dvr_password: "your_dvr_password"
    num_channels: 4
"""

import base64
import json
import time
from datetime import datetime
from typing import Any

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from jarvis.agents.base_agent import BaseAgent


class CameraAgent(BaseAgent):
    name = "camera"
    description = (
        "Connects directly to Hikvision DVR/NVR via its public IP address. "
        "Fetches live snapshots from any channel and analyses scenes with AI vision."
    )

    def __init__(self, cfg: dict, global_cfg: dict) -> None:
        super().__init__(cfg, global_cfg)
        self._ip: str       = cfg.get("dvr_ip", "")
        self._user: str     = cfg.get("dvr_username", "admin")
        self._pass: str     = cfg.get("dvr_password", "")
        self._channels: int = int(cfg.get("num_channels", 4))
        self._port: int     = int(cfg.get("port", 80))
        self._base: str     = f"http://{self._ip}:{self._port}" if self._port != 80 else f"http://{self._ip}"

    # ── Tools ────────────────────────────────────────────────────────────────

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "list_cameras",
                "description": "Lists all camera channels on the DVR/NVR with their status.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "get_camera_snapshot",
                "description": "Captures a live snapshot from a DVR channel.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "channel": {
                            "type": "integer",
                            "description": "Channel number (1, 2, 3, 4...)",
                        },
                    },
                    "required": ["channel"],
                },
            },
            {
                "name": "analyze_camera",
                "description": (
                    "Captures a live snapshot from a DVR channel and uses AI vision "
                    "to describe what is happening — people, vehicles, security concerns."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "channel": {
                            "type": "integer",
                            "description": "Channel number (1, 2, 3, 4...)",
                        },
                        "focus": {
                            "type": "string",
                            "enum": ["security", "people", "vehicles", "general"],
                        },
                    },
                    "required": ["channel"],
                },
            },
            {
                "name": "get_rtsp_url",
                "description": "Returns the RTSP stream URL for a DVR channel (open in VLC).",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "channel": {"type": "integer"},
                        "stream": {
                            "type": "string",
                            "enum": ["main", "sub"],
                            "description": "main = high quality, sub = low bandwidth",
                        },
                    },
                    "required": ["channel"],
                },
            },
        ]

    # ── Dispatch ─────────────────────────────────────────────────────────────

    def handle(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name == "list_cameras":       return self._list()
        if tool_name == "get_camera_snapshot": return self._snapshot(tool_input)
        if tool_name == "analyze_camera":     return self._analyze(tool_input)
        if tool_name == "get_rtsp_url":       return self._rtsp(tool_input)
        return f"Unknown tool: {tool_name}"

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _no_cfg(self) -> str:
        return json.dumps({
            "error": "DVR IP/credentials not configured.",
            "fix": (
                "Add to config.local.yaml:\n"
                "camera:\n"
                "  dvr_ip: '41.84.157.45'\n"
                "  dvr_username: 'admin'\n"
                "  dvr_password: 'your_password'\n"
                "  num_channels: 4"
            ),
        })

    def _fetch_snapshot(self, channel: int) -> bytes | None:
        """Fetch snapshot via Hikvision ISAPI — tries Basic then Digest auth."""
        # ISAPI snapshot endpoint
        url = f"{self._base}/ISAPI/Streaming/channels/{channel}01/picture"
        for auth in (HTTPDigestAuth(self._user, self._pass),
                     HTTPBasicAuth(self._user, self._pass)):
            try:
                resp = requests.get(url, auth=auth, timeout=10, stream=True)
                if resp.status_code == 200 and "image" in resp.headers.get("Content-Type", ""):
                    return resp.content
            except Exception:
                continue

        # Fallback: older CGI snapshot endpoint
        cgi_url = f"{self._base}/cgi-bin/snapshot.cgi?channel={channel}"
        for auth in (HTTPDigestAuth(self._user, self._pass),
                     HTTPBasicAuth(self._user, self._pass)):
            try:
                resp = requests.get(cgi_url, auth=auth, timeout=10)
                if resp.status_code == 200 and len(resp.content) > 1000:
                    return resp.content
            except Exception:
                continue
        return None

    # ── Implementations ───────────────────────────────────────────────────────

    def _list(self) -> str:
        if not self._ip or not self._pass:
            return self._no_cfg()
        channels = []
        for ch in range(1, self._channels + 1):
            img = self._fetch_snapshot(ch)
            channels.append({
                "channel": ch,
                "name": f"Channel {ch}",
                "status": "ONLINE" if img else "OFFLINE/NO SIGNAL",
                "rtsp_main": f"rtsp://{self._user}:{self._pass}@{self._ip}:554/Streaming/Channels/{ch}01",
            })
        online = sum(1 for c in channels if "ONLINE" in c["status"])
        return json.dumps({
            "dvr_ip":  self._ip,
            "channels": channels,
            "online":  online,
            "total":   self._channels,
        })

    def _snapshot(self, inp: dict) -> str:
        if not self._ip or not self._pass:
            return self._no_cfg()
        ch = int(inp.get("channel", 1))
        img = self._fetch_snapshot(ch)
        if not img:
            return json.dumps({
                "error": f"Channel {ch} did not return an image.",
                "hint": "Check DVR password and that the channel has a connected camera.",
            })
        return json.dumps({
            "channel":   ch,
            "timestamp": datetime.now().isoformat(),
            "size_kb":   round(len(img) / 1024, 1),
            "status":    "captured",
        })

    def _analyze(self, inp: dict) -> str:
        if not self._ip or not self._pass:
            return self._no_cfg()
        ch = int(inp.get("channel", 1))
        img = self._fetch_snapshot(ch)
        if not img:
            return json.dumps({
                "error": f"Could not fetch snapshot from channel {ch}.",
                "hint": "Verify DVR password and channel has a camera connected.",
            })
        b64 = base64.b64encode(img).decode()
        focus = inp.get("focus", "security")
        prompts = {
            "security": "Identify security concerns: intruders, suspicious activity, unauthorized access.",
            "people":   "Describe all people visible — clothing, activity, direction of movement.",
            "vehicles": "Identify vehicles — type, colour, licence plate if visible.",
            "general":  "Describe the full scene in detail.",
        }
        return json.dumps({
            "channel":   ch,
            "dvr_ip":    self._ip,
            "timestamp": datetime.now().isoformat(),
            "image_base64": b64,
            "analysis_instruction": (
                f"Analyse this live security camera image from channel {ch} "
                f"at the remote site ({self._ip}). "
                f"{prompts.get(focus, prompts['general'])} "
                "Be specific and note any anomalies."
            ),
        })

    def _rtsp(self, inp: dict) -> str:
        ch = int(inp.get("channel", 1))
        stream = "01" if inp.get("stream", "main") == "main" else "02"
        url = f"rtsp://{self._user}:{self._pass}@{self._ip}:554/Streaming/Channels/{ch}{stream}"
        return json.dumps({
            "channel":    ch,
            "rtsp_url":   url,
            "vlc_tip":    "Open VLC → Media → Open Network Stream → paste the URL",
            "dashboard":  f"http://{self._ip}/doc/page/preview.asp",
        })
