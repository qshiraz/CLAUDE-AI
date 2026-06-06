"""Camera agent — Guard Vision / IP camera integration with Claude vision analysis."""

import base64
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from jarvis.agents.base_agent import BaseAgent

_alerts: list[dict] = []
_alert_lock = threading.Lock()
_MAX_ALERTS = 50


class CameraAgent(BaseAgent):
    name = "camera"
    description = (
        "Monitors Guard Vision / IP cameras, captures snapshots, "
        "analyses scenes with AI vision, and tracks motion alerts."
    )

    def __init__(self, cfg: dict, global_cfg: dict) -> None:
        super().__init__(cfg, global_cfg)
        self._cameras: list[dict] = cfg.get("cameras", [])
        self._snapshot_dir = Path(cfg.get("snapshot_dir", "/tmp/jarvis_snapshots"))
        self._snapshot_dir.mkdir(parents=True, exist_ok=True)

    # ── Tool schema ──────────────────────────────────────────────────────────

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "list_cameras",
                "description": "Lists all configured cameras with their status and last-seen timestamp.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "get_camera_snapshot",
                "description": (
                    "Captures a still image from a camera and returns metadata. "
                    "For HTTP snapshot cameras the image is fetched; for RTSP "
                    "cameras a frame is captured if OpenCV is available."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "camera_id": {
                            "type": "string",
                            "description": "Camera ID or name as listed by list_cameras",
                        },
                    },
                    "required": ["camera_id"],
                },
            },
            {
                "name": "analyze_camera_scene",
                "description": (
                    "Captures a snapshot from the camera and uses Claude's vision "
                    "to describe what is happening — people, vehicles, activity, "
                    "potential security concerns."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "camera_id": {"type": "string", "description": "Camera ID or name"},
                        "focus": {
                            "type": "string",
                            "description": "What to focus on: 'security', 'people', 'vehicles', 'general'",
                        },
                    },
                    "required": ["camera_id"],
                },
            },
            {
                "name": "get_motion_alerts",
                "description": "Returns recent motion/security alerts detected across all cameras.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Max alerts to return (default 10)",
                        },
                        "camera_id": {
                            "type": "string",
                            "description": "Filter by camera ID (optional)",
                        },
                    },
                },
            },
            {
                "name": "add_camera",
                "description": "Adds a new camera to the monitoring system at runtime.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "camera_id": {"type": "string", "description": "Unique ID for this camera"},
                        "name": {"type": "string", "description": "Human-readable name e.g. 'Front Gate'"},
                        "url": {
                            "type": "string",
                            "description": (
                                "Camera URL: RTSP stream (rtsp://...) or HTTP snapshot URL "
                                "(http://camera-ip/snapshot.jpg)"
                            ),
                        },
                        "username": {"type": "string", "description": "Camera username (if needed)"},
                        "password": {"type": "string", "description": "Camera password (if needed)"},
                        "location": {"type": "string", "description": "Physical location e.g. 'Main Entrance'"},
                    },
                    "required": ["camera_id", "name", "url"],
                },
            },
            {
                "name": "remove_camera",
                "description": "Removes a camera from monitoring.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "camera_id": {"type": "string"},
                    },
                    "required": ["camera_id"],
                },
            },
        ]

    # ── Dispatch ─────────────────────────────────────────────────────────────

    def handle(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name == "list_cameras":         return self._list_cameras()
        if tool_name == "get_camera_snapshot":  return self._snapshot(tool_input)
        if tool_name == "analyze_camera_scene": return self._analyze(tool_input)
        if tool_name == "get_motion_alerts":    return self._alerts(tool_input)
        if tool_name == "add_camera":           return self._add_camera(tool_input)
        if tool_name == "remove_camera":        return self._remove_camera(tool_input)
        return f"Unknown tool: {tool_name}"

    # ── Implementations ───────────────────────────────────────────────────────

    def _list_cameras(self) -> str:
        if not self._cameras:
            return json.dumps({
                "cameras": [],
                "message": (
                    "No cameras configured yet. "
                    "Add cameras via the add_camera tool or under camera.cameras in config.local.yaml."
                ),
            })
        result = []
        for cam in self._cameras:
            result.append({
                "id": cam.get("id", "unknown"),
                "name": cam.get("name", "Camera"),
                "location": cam.get("location", ""),
                "type": "RTSP" if cam.get("url", "").startswith("rtsp://") else "HTTP",
                "url_masked": self._mask_url(cam.get("url", "")),
                "status": "configured",
            })
        return json.dumps({"cameras": result, "total": len(result)})

    def _snapshot(self, inp: dict) -> str:
        cam = self._find_camera(inp["camera_id"])
        if not cam:
            return json.dumps({"error": f"Camera '{inp['camera_id']}' not found. Use list_cameras to see configured cameras."})

        url = cam.get("url", "")
        username = cam.get("username", "")
        password = cam.get("password", "")
        filename = f"{cam['id']}_{int(time.time())}.jpg"
        out_path = self._snapshot_dir / filename

        try:
            if url.startswith("http"):
                img_data = self._fetch_http_snapshot(url, username, password)
                if img_data:
                    out_path.write_bytes(img_data)
                    return json.dumps({
                        "camera": cam["name"],
                        "snapshot_path": str(out_path),
                        "timestamp": datetime.now().isoformat(),
                        "size_kb": round(len(img_data) / 1024, 1),
                        "status": "captured",
                    })
            elif url.startswith("rtsp://"):
                result = self._capture_rtsp_frame(url, username, password, out_path)
                if result:
                    return json.dumps({
                        "camera": cam["name"],
                        "snapshot_path": str(out_path),
                        "timestamp": datetime.now().isoformat(),
                        "status": "captured",
                    })
        except Exception as exc:
            return json.dumps({"error": f"Snapshot failed: {exc}", "camera": cam["name"]})

        return json.dumps({"error": "Could not capture snapshot — check camera URL and credentials."})

    def _analyze(self, inp: dict) -> str:
        cam = self._find_camera(inp["camera_id"])
        if not cam:
            return json.dumps({"error": f"Camera '{inp['camera_id']}' not found."})

        focus = inp.get("focus", "security")
        url = cam.get("url", "")
        username = cam.get("username", "")
        password = cam.get("password", "")

        img_data: bytes | None = None
        try:
            if url.startswith("http"):
                img_data = self._fetch_http_snapshot(url, username, password)
            elif url.startswith("rtsp://"):
                tmp = self._snapshot_dir / f"analyze_{cam['id']}.jpg"
                if self._capture_rtsp_frame(url, username, password, tmp):
                    img_data = tmp.read_bytes()
        except Exception as exc:
            return json.dumps({"error": f"Could not fetch image: {exc}"})

        if img_data is None:
            return json.dumps({
                "camera": cam["name"],
                "analysis_instruction": (
                    f"No live image available from camera '{cam['name']}' ({cam.get('location','')}) at this time. "
                    f"Tell the user the camera is configured but the snapshot could not be fetched — "
                    "check network connectivity and camera credentials. "
                    f"The camera URL type is: {'RTSP stream' if url.startswith('rtsp') else 'HTTP snapshot'}."
                ),
            })

        b64 = base64.b64encode(img_data).decode()
        focus_prompts = {
            "security": "Identify any security concerns: intruders, suspicious activity, vehicles, access points.",
            "people": "Count and describe people present — gender, approximate age, clothing, activity.",
            "vehicles": "Identify vehicles: type, colour, any visible licence plate text.",
            "general": "Describe the scene in detail.",
        }
        prompt = focus_prompts.get(focus, focus_prompts["general"])

        return json.dumps({
            "camera": cam["name"],
            "location": cam.get("location", ""),
            "timestamp": datetime.now().isoformat(),
            "image_base64": b64,
            "analysis_instruction": (
                f"Analyse this security camera image from '{cam['name']}' "
                f"({cam.get('location','')}, Mombasa). {prompt} "
                "Be specific and security-focused. Note any anomalies."
            ),
        })

    def _alerts(self, inp: dict) -> str:
        limit = int(inp.get("limit", 10))
        filter_cam = inp.get("camera_id")
        with _alert_lock:
            alerts = list(_alerts)
        if filter_cam:
            alerts = [a for a in alerts if a.get("camera_id") == filter_cam]
        alerts = sorted(alerts, key=lambda a: a.get("timestamp", ""), reverse=True)[:limit]
        return json.dumps({"alerts": alerts, "total": len(alerts)})

    def _add_camera(self, inp: dict) -> str:
        new_cam = {
            "id": inp["camera_id"],
            "name": inp["name"],
            "url": inp["url"],
            "location": inp.get("location", ""),
            "username": inp.get("username", ""),
            "password": inp.get("password", ""),
        }
        self._cameras = [c for c in self._cameras if c.get("id") != inp["camera_id"]]
        self._cameras.append(new_cam)
        return json.dumps({
            "added": True,
            "camera": inp["name"],
            "id": inp["camera_id"],
            "note": "Camera added for this session. To persist it, add it to config.local.yaml under camera.cameras.",
        })

    def _remove_camera(self, inp: dict) -> str:
        before = len(self._cameras)
        self._cameras = [c for c in self._cameras if c.get("id") != inp["camera_id"]]
        removed = len(self._cameras) < before
        return json.dumps({"removed": removed, "camera_id": inp["camera_id"]})

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _find_camera(self, camera_id: str) -> dict | None:
        for cam in self._cameras:
            if cam.get("id") == camera_id or cam.get("name", "").lower() == camera_id.lower():
                return cam
        return None

    @staticmethod
    def _mask_url(url: str) -> str:
        if "@" in url:
            scheme, rest = url.split("://", 1)
            credentials, host = rest.split("@", 1)
            return f"{scheme}://****:****@{host}"
        return url

    @staticmethod
    def _fetch_http_snapshot(url: str, username: str, password: str) -> bytes | None:
        try:
            import requests  # type: ignore
            auth = (username, password) if username else None
            resp = requests.get(url, auth=auth, timeout=8, stream=True)
            resp.raise_for_status()
            return resp.content
        except Exception:
            return None

    @staticmethod
    def _capture_rtsp_frame(url: str, username: str, password: str, out_path: Path) -> bool:
        try:
            import cv2  # type: ignore
            if username:
                proto, rest = url.split("://", 1)
                url = f"{proto}://{username}:{password}@{rest}"
            cap = cv2.VideoCapture(url)
            ret, frame = cap.read()
            cap.release()
            if ret:
                cv2.imwrite(str(out_path), frame)
                return True
        except ImportError:
            pass
        except Exception:
            pass
        return False


def add_motion_alert(camera_id: str, camera_name: str, description: str) -> None:
    """Called externally to inject a motion alert (e.g. from a webhook)."""
    with _alert_lock:
        _alerts.append({
            "camera_id": camera_id,
            "camera_name": camera_name,
            "description": description,
            "timestamp": datetime.now().isoformat(),
        })
        if len(_alerts) > _MAX_ALERTS:
            _alerts.pop(0)
