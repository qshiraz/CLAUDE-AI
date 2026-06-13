"""Hikvision DVR/NVR direct agent — connects via public IP using ISAPI.

Add to config.local.yaml:
  camera:
    dvr_ip: "41.84.157.45"
    dvr_username: "admin"
    dvr_password: "your_password"
    channels:
      1: "Front Gate"
      2: "Parking"
      3: "Main Entrance"
      4: "Server Room"
"""

import base64
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from jarvis.agents.base_agent import BaseAgent

_NAMES_FILE = Path.home() / ".kamran_camera_names.json"

def _load_saved_names() -> dict[int, str]:
    try:
        return {int(k): v for k, v in json.loads(_NAMES_FILE.read_text()).items()}
    except Exception:
        return {}

def _save_name(channel: int, name: str) -> None:
    names = _load_saved_names()
    names[channel] = name
    _NAMES_FILE.write_text(json.dumps({str(k): v for k, v in names.items()}))


class CameraAgent(BaseAgent):
    name = "camera"
    description = (
        "Connects directly to the Videoteknika/Hikvision DVR via its public IP. "
        "Lists cameras by name, fetches live snapshots, and analyses scenes with AI."
    )

    def __init__(self, cfg: dict, global_cfg: dict) -> None:
        super().__init__(cfg, global_cfg)
        self._ip   = cfg.get("dvr_ip", "")
        self._user = cfg.get("dvr_username", "admin")
        self._pwd  = cfg.get("dvr_password", "")
        self._port = int(cfg.get("port", 80))
        self._base = f"http://{self._ip}" if self._port == 80 else f"http://{self._ip}:{self._port}"

        # Named channels: {channel_num: name}
        raw = cfg.get("channels", {})
        if isinstance(raw, dict):
            self._named = {int(k): v for k, v in raw.items()}
        else:
            # If channels is a list like [{id:1, name:"Front Gate"}]
            self._named = {c["id"]: c["name"] for c in raw if "id" in c}

        # Fallback: generate Channel N names up to num_channels
        num = int(cfg.get("num_channels", max(self._named.keys(), default=4)))
        for i in range(1, num + 1):
            self._named.setdefault(i, f"Channel {i}")

        # User-saved names override config names
        for ch, nm in _load_saved_names().items():
            self._named[ch] = nm

    # ── Tools ────────────────────────────────────────────────────────────────

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "list_cameras",
                "description": "Lists all cameras by name with their live status.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "show_camera",
                "description": (
                    "Shows a live feed from a named camera. "
                    "Use this when the user asks to see a specific camera by name."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "camera_name": {
                            "type": "string",
                            "description": "Camera name e.g. 'Front Gate', 'Parking', or channel number",
                        },
                    },
                    "required": ["camera_name"],
                },
            },
            {
                "name": "analyze_camera",
                "description": "Captures a live snapshot and uses AI vision to describe the scene.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "camera_name": {
                            "type": "string",
                            "description": "Camera name or channel number",
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
                "name": "rename_camera",
                "description": (
                    "Assigns a custom name to a camera channel and saves it permanently. "
                    "Use when the user says things like 'channel 2 is the front gate' or "
                    "'call camera 5 Parking'."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "channel": {
                            "type": "integer",
                            "description": "The channel number to rename",
                        },
                        "name": {
                            "type": "string",
                            "description": "The new human-friendly name for this camera",
                        },
                    },
                    "required": ["channel", "name"],
                },
            },
            {
                "name": "get_rtsp_url",
                "description": "Returns the RTSP stream URL for VLC or other players.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "camera_name": {"type": "string"},
                        "stream": {"type": "string", "enum": ["main", "sub"]},
                    },
                    "required": ["camera_name"],
                },
            },
        ]

    # ── Dispatch ─────────────────────────────────────────────────────────────

    def handle(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name == "list_cameras":   return self._list()
        if tool_name == "show_camera":    return self._show(tool_input)
        if tool_name == "analyze_camera": return self._analyze(tool_input)
        if tool_name == "get_rtsp_url":   return self._rtsp(tool_input)
        if tool_name == "rename_camera":  return self._rename(tool_input)
        return f"Unknown tool: {tool_name}"

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _no_cfg(self) -> str:
        return json.dumps({
            "error": "DVR not configured.",
            "fix": "Add dvr_ip, dvr_username, dvr_password, and channels to config.local.yaml under camera:",
        })

    def _find_channel(self, name: str) -> int | None:
        """Find channel number by name or number string."""
        # Direct number
        try:
            ch = int(name)
            if ch in self._named:
                return ch
        except ValueError:
            pass
        # Name match (case-insensitive, partial ok)
        low = name.lower().strip()
        for ch, nm in self._named.items():
            if low in nm.lower() or nm.lower() in low:
                return ch
        return None

    def _snapshot(self, channel: int, timeout: int = 4) -> bytes | None:
        urls = [
            f"{self._base}/ISAPI/Streaming/channels/{channel}01/picture",
            f"{self._base}/cgi-bin/snapshot.cgi?channel={channel}",
        ]
        for url in urls:
            for auth in (HTTPDigestAuth(self._user, self._pwd),
                         HTTPBasicAuth(self._user, self._pwd)):
                try:
                    r = requests.get(url, auth=auth, timeout=timeout)
                    if r.status_code == 200 and len(r.content) > 500:
                        return r.content
                except Exception:
                    continue
        return None

    def _ping(self, channel: int) -> bool:
        """Quick HEAD/GET just to check if channel is online — no image data needed."""
        url = f"{self._base}/ISAPI/Streaming/channels/{channel}01/picture"
        for auth in (HTTPDigestAuth(self._user, self._pwd),
                     HTTPBasicAuth(self._user, self._pwd)):
            try:
                r = requests.get(url, auth=auth, timeout=3, stream=True)
                r.close()
                if r.status_code == 200:
                    return True
            except Exception:
                continue
        return False

    # ── Implementations ───────────────────────────────────────────────────────

    def _list(self) -> str:
        if not self._ip or not self._pwd:
            return self._no_cfg()
        channels = sorted(self._named.items())

        # Check all channels in parallel
        results: dict[int, bool] = {}
        with ThreadPoolExecutor(max_workers=len(channels)) as pool:
            futures = {pool.submit(self._ping, ch): ch for ch, _ in channels}
            for fut in as_completed(futures):
                ch = futures[fut]
                results[ch] = fut.result()

        cameras = [
            {
                "channel":    ch,
                "name":       name,
                "status":     "ONLINE" if results.get(ch) else "OFFLINE",
                "stream_url": f"/api/cameras/stream/{ch}",
            }
            for ch, name in channels
        ]
        online = sum(1 for c in cameras if c["status"] == "ONLINE")
        return json.dumps({
            "cameras": cameras,
            "online":  online,
            "total":   len(cameras),
            "dvr_ip":  self._ip,
        })

    def _show(self, inp: dict) -> str:
        if not self._ip or not self._pwd:
            return self._no_cfg()
        ch = self._find_channel(inp["camera_name"])
        if ch is None:
            names = list(self._named.values())
            return json.dumps({
                "error": f"Camera '{inp['camera_name']}' not found.",
                "available": names,
            })
        name = self._named[ch]
        return json.dumps({
            "action":      "show_feed",
            "channel":     ch,
            "camera_name": name,
            "stream_url":  f"/api/cameras/stream/{ch}",
            "snapshot_url": f"/api/cameras/snapshot/{ch}",
            "message": f"Switching to {name} — Channel {ch}. Live feed is now active.",
        })

    def _analyze(self, inp: dict) -> str:
        if not self._ip or not self._pwd:
            return self._no_cfg()
        ch = self._find_channel(inp["camera_name"])
        if ch is None:
            return json.dumps({"error": f"Camera '{inp['camera_name']}' not found."})
        img = self._snapshot(ch)
        name = self._named[ch]
        if not img:
            return json.dumps({"error": f"{name} is offline or not returning images."})
        b64 = base64.b64encode(img).decode()
        focus = inp.get("focus", "security")
        prompts = {
            "security": "Identify security concerns: intruders, suspicious activity, unauthorized access.",
            "people":   "Describe all people — clothing, activity, direction of movement.",
            "vehicles": "Identify vehicles — type, colour, licence plate if visible.",
            "general":  "Describe the full scene in detail.",
        }
        return json.dumps({
            "channel":   ch,
            "camera":    name,
            "timestamp": datetime.now().isoformat(),
            "image_base64": b64,
            "analysis_instruction": (
                f"Analyse this live security camera image from '{name}' (Channel {ch}). "
                f"{prompts.get(focus, prompts['general'])} Be specific and note any anomalies."
            ),
        })

    def _rtsp(self, inp: dict) -> str:
        ch = self._find_channel(inp["camera_name"])
        if ch is None:
            return json.dumps({"error": "Camera not found."})
        stream = "01" if inp.get("stream", "main") == "main" else "02"
        name = self._named[ch]
        return json.dumps({
            "camera":   name,
            "channel":  ch,
            "rtsp_url": f"rtsp://{self._user}:{self._pwd}@{self._ip}:554/Streaming/Channels/{ch}{stream}",
            "tip":      "Open VLC → Media → Open Network Stream → paste URL",
        })

    def _rename(self, inp: dict) -> str:
        ch = int(inp["channel"])
        name = inp["name"].strip()
        old_name = self._named.get(ch, f"Channel {ch}")
        self._named[ch] = name
        _save_name(ch, name)
        return json.dumps({
            "action":    "camera_renamed",
            "channel":   ch,
            "old_name":  old_name,
            "new_name":  name,
            "message":   f"Channel {ch} is now saved as '{name}'.",
        })
