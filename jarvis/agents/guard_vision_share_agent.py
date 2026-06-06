"""Guard Vision Share agent — connects via QR share token, no login needed.

The QR code contains: HC_DEVICE_GROUP_SHARE:{"qrId":"...","isEncrypt":false}
Extract the qrId and add it to config.local.yaml under guard_vision_share.
"""

import base64
import json
import time
from datetime import datetime
from typing import Any

import requests

from jarvis.agents.base_agent import BaseAgent


class GuardVisionShareAgent(BaseAgent):
    name = "guard_vision_share"
    description = (
        "Connects to Guard Vision cameras using a shared QR token — "
        "no login or password required. Lists cameras, fetches live snapshots, "
        "and analyses scenes with AI vision."
    )

    # Only reachable Hikvision endpoints (confirmed by connectivity test)
    _BASES = [
        "https://api.hik-connect.com",
        "https://apiisa.hik-connect.com",   # Africa/International South
    ]

    # All known API paths for share QR resolution — tried in order
    _SHARE_PATHS = [
        "/v3/share/device/group/cameras",
        "/v3/share/devicegroup/cameras",
        "/v3/share/cameras",
        "/v3/userdevices/v1/share/cameras",
        "/v3/share/device/group/qrcode/cameras",
    ]

    def __init__(self, cfg: dict, global_cfg: dict) -> None:
        super().__init__(cfg, global_cfg)
        self._qr_id: str = cfg.get("qr_id", "")
        self._cam_cache: list[dict] = []
        self._share_token: str = ""
        self._cache_ts: float = 0.0

    # ── Tools ────────────────────────────────────────────────────────────────

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "gvs_list_cameras",
                "description": "Lists all cameras accessible via the Guard Vision share QR code.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "gvs_analyze_camera",
                "description": (
                    "Fetches a live snapshot from a shared Guard Vision camera "
                    "and uses AI vision to describe what is happening — "
                    "people, vehicles, activity, security concerns."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "camera_name": {
                            "type": "string",
                            "description": "Camera name as shown in Guard Vision app, or 'first' for the first camera",
                        },
                        "focus": {
                            "type": "string",
                            "enum": ["security", "people", "vehicles", "general"],
                            "description": "What to focus on. Default: security",
                        },
                    },
                    "required": ["camera_name"],
                },
            },
            {
                "name": "gvs_snapshot",
                "description": "Takes a snapshot from a Guard Vision camera and returns metadata.",
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
        if tool_name == "gvs_list_cameras":  return self._list_cameras()
        if tool_name == "gvs_analyze_camera": return self._analyze(tool_input)
        if tool_name == "gvs_snapshot":       return self._snapshot(tool_input)
        return f"Unknown tool: {tool_name}"

    # ── Share API ─────────────────────────────────────────────────────────────

    def _no_qr_msg(self) -> str:
        return json.dumps({
            "error": "Guard Vision share QR ID not configured.",
            "fix": (
                "Add to config.local.yaml:\n\n"
                "guard_vision_share:\n"
                "  qr_id: 'your_qr_id_here'\n\n"
                "Get the qrId by scanning your Guard Vision share QR code."
            ),
        })

    def _resolve_share(self) -> list[dict]:
        """Use the qrId to get camera list and access token."""
        if self._cam_cache and time.time() - self._cache_ts < 300:
            return self._cam_cache

        headers = {
            "clientType":   "55",
            "lang":         "en-US",
            "Content-Type": "application/json",
        }
        attempts: list[str] = []

        for base in self._BASES:
            for path in self._SHARE_PATHS:
                url = f"{base}{path}"
                # Try GET
                for method in ("GET", "POST"):
                    try:
                        if method == "GET":
                            resp = requests.get(
                                url,
                                params={"qrId": self._qr_id, "pageStart": 0, "pageSize": 100},
                                headers=headers,
                                timeout=10,
                                allow_redirects=True,
                            )
                        else:
                            resp = requests.post(
                                url,
                                json={"qrId": self._qr_id},
                                headers=headers,
                                timeout=10,
                                allow_redirects=True,
                            )

                        if resp.status_code not in (200, 302):
                            attempts.append(f"{method} {url} → HTTP {resp.status_code}")
                            continue

                        try:
                            data = resp.json()
                        except Exception:
                            attempts.append(f"{method} {url} → non-JSON response")
                            continue

                        code = str(data.get("code", ""))
                        if code == "200":
                            cams = (
                                data.get("cameraInfoList") or
                                data.get("list") or
                                (data.get("data") or {}).get("cameraInfoList") or
                                (data.get("data") or {}).get("list") or []
                            )
                            if isinstance(cams, list):
                                self._cam_cache = cams
                                self._cache_ts = time.time()
                                self._share_token = (
                                    data.get("shareToken") or
                                    (data.get("data") or {}).get("shareToken") or ""
                                )
                                return self._cam_cache

                        attempts.append(f"{method} {url} → code={code} msg={data.get('msg','')}")
                    except Exception as exc:
                        attempts.append(f"{method} {url} → {type(exc).__name__}: {exc}")

        raise RuntimeError(
            "Could not resolve Guard Vision share. Attempts:\n" + "\n".join(attempts[-6:])
        )

    def _fetch_snapshot(self, cam: dict) -> bytes | None:
        index_code = (cam.get("cameraIndexCode") or
                      cam.get("deviceIndexCode") or
                      cam.get("deviceSerial", ""))

        params = {
            "qrId": self._qr_id,
            "cameraIndexCode": index_code,
            "channelNo": 1,
        }
        if self._share_token:
            params["shareToken"] = self._share_token

        for base in self._BASES:
            try:
                # Try snapshot via share endpoint
                resp = requests.get(
                    f"{base}/v3/share/snapshots/cameras/{index_code}/files",
                    params=params,
                    headers={"clientType": "55", "lang": "en-US"},
                    timeout=12,
                )
                data = resp.json()
                img_url = (data.get("picUrl") or
                           (data.get("data") or {}).get("url") or
                           data.get("url") or "")
                if img_url:
                    img = requests.get(img_url, timeout=15)
                    img.raise_for_status()
                    return img.content
            except Exception:
                continue
        return None

    def _find_cam(self, name: str) -> dict | None:
        if name.lower() in ("first", "any", "1"):
            return self._cam_cache[0] if self._cam_cache else None
        low = name.lower().strip()
        for c in self._cam_cache:
            cam_name = (c.get("cameraName") or c.get("deviceName") or "").lower()
            if cam_name == low or low in cam_name:
                return c
        return None

    # ── Implementations ───────────────────────────────────────────────────────

    def _list_cameras(self) -> str:
        if not self._qr_id:
            return self._no_qr_msg()
        try:
            cams = self._resolve_share()
            result = [{
                "name":   c.get("cameraName") or c.get("deviceName") or f"Camera {i+1}",
                "model":  c.get("deviceType", "Hikvision"),
                "serial": c.get("deviceSerial", ""),
                "status": "ONLINE" if str(c.get("status", "0")) in ("1", "online") else "OFFLINE",
            } for i, c in enumerate(cams)]
            return json.dumps({
                "cameras": result,
                "total":   len(result),
                "source":  "Guard Vision Share QR",
            })
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    def _snapshot(self, inp: dict) -> str:
        if not self._qr_id:
            return self._no_qr_msg()
        try:
            self._resolve_share()
            cam = self._find_cam(inp["camera_name"])
            if not cam:
                names = [c.get("cameraName") or c.get("deviceName") for c in self._cam_cache]
                return json.dumps({"error": f"Camera not found. Available: {names}"})
            img = self._fetch_snapshot(cam)
            if img:
                return json.dumps({
                    "camera":    cam.get("cameraName", inp["camera_name"]),
                    "timestamp": datetime.now().isoformat(),
                    "size_kb":   round(len(img) / 1024, 1),
                    "status":    "captured",
                })
            return json.dumps({"error": "Snapshot not available — camera may be offline."})
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    def _analyze(self, inp: dict) -> str:
        if not self._qr_id:
            return self._no_qr_msg()
        try:
            self._resolve_share()
            cam = self._find_cam(inp["camera_name"])
            if not cam:
                names = [c.get("cameraName") or c.get("deviceName") for c in self._cam_cache]
                return json.dumps({"error": f"Camera not found. Available: {names}"})

            img = self._fetch_snapshot(cam)
            cam_name = cam.get("cameraName") or cam.get("deviceName") or inp["camera_name"]

            if not img:
                return json.dumps({
                    "camera": cam_name,
                    "error": "Could not fetch snapshot — camera may be offline or streaming disabled.",
                })

            b64 = base64.b64encode(img).decode()
            focus = inp.get("focus", "security")
            prompts = {
                "security": "Identify any security concerns: intruders, suspicious activity, unauthorized vehicles.",
                "people":   "Describe all people visible — clothing, activity, direction of movement.",
                "vehicles": "Identify vehicles — type, colour, any visible licence plate.",
                "general":  "Describe the full scene in detail.",
            }
            return json.dumps({
                "camera":    cam_name,
                "timestamp": datetime.now().isoformat(),
                "image_base64": b64,
                "analysis_instruction": (
                    f"Analyse this live security camera image from '{cam_name}' "
                    f"at the remote site (Mombasa, Kenya area). "
                    f"{prompts.get(focus, prompts['general'])} "
                    "Be specific and concise. Note any anomalies or concerns."
                ),
            })
        except Exception as exc:
            return json.dumps({"error": str(exc)})
