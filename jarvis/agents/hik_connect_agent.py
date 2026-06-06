"""Hik-Connect / Guard Vision credential-based agent.

Logs in with Guard Vision username + password — no developer account needed.
Handles Hikvision's new-device verification code flow automatically.

Add to config.local.yaml:
  hik_connect:
    username: "your_guard_vision_email_or_phone"
    password: "your_guard_vision_password"
"""

import base64
import hashlib
import json
import time
from datetime import datetime
from typing import Any

import requests

from jarvis.agents.base_agent import BaseAgent

# Persistent across the process lifetime
_SESSION: dict = {}   # session_id, expiry
_PENDING_VERIFY: dict = {}  # waiting for SMS/email code


class HikConnectAgent(BaseAgent):
    name = "hik_connect"
    description = (
        "Connects to Guard Vision / Hik-Connect cloud cameras using your "
        "account credentials. Lists cameras, fetches snapshots, and analyses scenes."
    )

    # Hikvision uses region-specific endpoints; try both
    _ENDPOINTS = [
        "https://api.hik-connect.com",
        "https://api2.hik-connect.com",
        "https://api.hikvision.com",
    ]
    _BASE = "https://api.hik-connect.com"

    # clientType 55 = third-party; featureCode must be stable per "device"
    _FEATURE_CODE = "a1b2c3d4e5f6a7b8"
    _CLIENT_TYPE  = "55"

    def __init__(self, cfg: dict, global_cfg: dict) -> None:
        super().__init__(cfg, global_cfg)
        self._username: str = cfg.get("username", "")
        self._password: str = cfg.get("password", "")
        self._session_id: str = ""
        self._session_expiry: float = 0.0
        self._cam_cache: list[dict] = []
        self._cache_ts: float = 0.0
        self._verify_pending: bool = False

    # ── Tool definitions ─────────────────────────────────────────────────────

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "hc_list_cameras",
                "description": "Lists all cameras in your Guard Vision / Hik-Connect account.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "hc_verify_login",
                "description": (
                    "Submits the verification code sent to your phone or email "
                    "by Hikvision when logging in from a new device. "
                    "Call this after hc_list_cameras reports a verification code is required."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "The 6-digit code from your SMS or email",
                        },
                    },
                    "required": ["code"],
                },
            },
            {
                "name": "hc_analyze_camera",
                "description": (
                    "Fetches a live snapshot from a Guard Vision camera "
                    "and uses AI vision to describe what is happening — "
                    "people, vehicles, activity, security concerns."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "camera_name": {
                            "type": "string",
                            "description": "Camera name exactly as shown in Guard Vision app",
                        },
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
                "name": "hc_get_stream",
                "description": "Returns a live HLS stream URL for a Guard Vision camera.",
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
        if tool_name == "hc_list_cameras":   return self._list_cameras()
        if tool_name == "hc_verify_login":   return self._verify_login(tool_input)
        if tool_name == "hc_analyze_camera": return self._analyze(tool_input)
        if tool_name == "hc_get_stream":     return self._get_stream(tool_input)
        return f"Unknown tool: {tool_name}"

    # ── Auth helpers ──────────────────────────────────────────────────────────

    def _has_creds(self) -> bool:
        return bool(self._username and self._password)

    def _no_creds_response(self) -> str:
        return json.dumps({
            "error": "Guard Vision credentials not configured.",
            "fix": (
                "Open config.local.yaml and add:\n\n"
                "hik_connect:\n"
                "  username: 'your_guardvision_email'\n"
                "  password: 'your_guardvision_password'\n\n"
                "Then restart Jarvis."
            ),
        })

    def _md5(self, text: str) -> str:
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    def _login(self) -> str:
        """Return a valid session ID, logging in if needed."""
        if self._session_id and time.time() < self._session_expiry:
            return self._session_id

        payload = {
            "account":     self._username,
            "password":    self._md5(self._password),
            "featureCode": self._FEATURE_CODE,
            "clientType":  self._CLIENT_TYPE,
            "lang":        "en-US",
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}

        last_err = ""
        for base in self._ENDPOINTS:
            try:
                resp = requests.post(
                    f"{base}/v3/users/login",
                    data=payload,
                    headers=headers,
                    timeout=12,
                )
                data = resp.json()
                code = str(data.get("code", ""))

                if code == "200":
                    session = (data.get("loginSession") or
                               data.get("mloginSession") or {})
                    sid = session.get("sessionId", "")
                    if sid:
                        self._BASE = base
                        self._session_id = sid
                        self._session_expiry = time.time() + 3500
                        self._verify_pending = False
                        return sid

                # Verification code required (new device)
                if code in ("10002", "10006", "1014", "1015", "-100"):
                    self._verify_pending = True
                    _PENDING_VERIFY["base"] = base
                    _PENDING_VERIFY["payload"] = payload
                    raise RuntimeError(
                        "VERIFY_REQUIRED: Hikvision sent a code to your phone/email. "
                        "Check your messages and tell Jarvis: "
                        "'My verification code is 123456'"
                    )

                last_err = f"Code {code}: {data.get('msg', 'unknown error')}"
            except RuntimeError:
                raise
            except Exception as exc:
                last_err = str(exc)
                continue

        raise RuntimeError(f"Login failed: {last_err}")

    def _verify_login(self, inp: dict) -> str:
        """Complete login with SMS/email verification code."""
        if not _PENDING_VERIFY:
            return json.dumps({"error": "No pending verification. Try listing cameras first."})

        code = str(inp.get("code", "")).strip()
        base = _PENDING_VERIFY.get("base", self._BASE)
        payload = dict(_PENDING_VERIFY.get("payload", {}))
        payload["smsCode"] = code
        payload["phoneCode"] = code

        try:
            resp = requests.post(
                f"{base}/v3/users/loginWithCaptcha",
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
                timeout=12,
            )
            data = resp.json()
            api_code = str(data.get("code", ""))

            if api_code == "200":
                session = data.get("loginSession") or data.get("mloginSession") or {}
                sid = session.get("sessionId", "")
                if sid:
                    self._BASE = base
                    self._session_id = sid
                    self._session_expiry = time.time() + 3500
                    self._verify_pending = False
                    _PENDING_VERIFY.clear()
                    return json.dumps({
                        "success": True,
                        "message": "Logged in successfully. You can now list and view your cameras.",
                    })

            return json.dumps({"error": f"Verification failed: {data.get('msg', api_code)}"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    def _authed_get(self, path: str, params: dict | None = None) -> dict:
        sid = self._login()
        headers = {
            "sessionId":  sid,
            "clientType": self._CLIENT_TYPE,
            "lang":       "en-US",
        }
        resp = requests.get(f"{self._BASE}{path}", headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    # ── Camera list ───────────────────────────────────────────────────────────

    def _refresh_cams(self) -> list[dict]:
        if self._cam_cache and time.time() - self._cache_ts < 300:
            return self._cam_cache
        data = self._authed_get(
            "/v3/userdevices/v1/devices/pagelist",
            {"pageStart": 0, "pageSize": 100, "deviceCategory": "1"},
        )
        cams = (data.get("cameraInfoList") or
                data.get("deviceInfos") or
                (data.get("data") or {}).get("list") or
                data.get("list") or [])
        self._cam_cache = cams if isinstance(cams, list) else []
        self._cache_ts = time.time()
        return self._cam_cache

    def _find_cam(self, name: str) -> dict | None:
        low = name.lower().strip()
        for c in self._cam_cache:
            cam_name = (c.get("cameraName") or c.get("deviceName") or "").lower()
            if cam_name == low or low in cam_name:
                return c
        return None

    # ── Tool implementations ──────────────────────────────────────────────────

    def _list_cameras(self) -> str:
        if not self._has_creds():
            return self._no_creds_response()
        try:
            cams = self._refresh_cams()
            result = [{
                "name":   c.get("cameraName") or c.get("deviceName") or "Unknown",
                "model":  c.get("deviceType", ""),
                "serial": c.get("deviceSerial", ""),
                "status": "ONLINE" if str(c.get("status", "0")) in ("1", "online") else "OFFLINE",
            } for c in cams]
            return json.dumps({"cameras": result, "total": len(result)})
        except RuntimeError as exc:
            msg = str(exc)
            if "VERIFY_REQUIRED" in msg:
                return json.dumps({
                    "status": "verification_required",
                    "message": msg.replace("VERIFY_REQUIRED: ", ""),
                })
            return json.dumps({"error": msg})
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    def _analyze(self, inp: dict) -> str:
        if not self._has_creds():
            return self._no_creds_response()
        try:
            self._refresh_cams()
            cam = self._find_cam(inp["camera_name"])
            if not cam:
                names = [c.get("cameraName") or c.get("deviceName") for c in self._cam_cache]
                return json.dumps({"error": f"Camera not found. Available cameras: {names}"})

            index_code = (cam.get("cameraIndexCode") or
                          cam.get("deviceIndexCode") or
                          cam.get("deviceSerial", ""))

            # Fetch snapshot
            snap = self._authed_get(
                f"/v3/snapshots/cameras/{index_code}/files",
                {"channelNo": 1, "qualityLevel": 1},
            )
            img_url = (snap.get("picUrl") or
                       (snap.get("data") or {}).get("url") or
                       snap.get("url") or "")

            if not img_url:
                return json.dumps({
                    "camera": cam.get("cameraName", ""),
                    "error":  "Snapshot not available — camera may be offline or streaming is disabled.",
                })

            img_resp = requests.get(img_url, timeout=15)
            img_resp.raise_for_status()
            b64 = base64.b64encode(img_resp.content).decode()

            focus = inp.get("focus", "security")
            prompts = {
                "security": "Identify security concerns: intruders, suspicious vehicles, unusual activity, access points.",
                "people":   "Describe all people — clothing, activity, direction of movement.",
                "vehicles": "Identify vehicles — type, colour, licence plate if visible.",
                "general":  "Describe the full scene in detail.",
            }
            return json.dumps({
                "camera":    cam.get("cameraName", inp["camera_name"]),
                "timestamp": datetime.now().isoformat(),
                "image_base64": b64,
                "analysis_instruction": (
                    f"Analyse this live security camera image from '{cam.get('cameraName', '')}'. "
                    f"{prompts.get(focus, prompts['general'])} "
                    "Be specific, note any anomalies."
                ),
            })
        except RuntimeError as exc:
            msg = str(exc)
            if "VERIFY_REQUIRED" in msg:
                return json.dumps({"status": "verification_required",
                                   "message": msg.replace("VERIFY_REQUIRED: ", "")})
            return json.dumps({"error": msg})
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    def _get_stream(self, inp: dict) -> str:
        if not self._has_creds():
            return self._no_creds_response()
        try:
            self._refresh_cams()
            cam = self._find_cam(inp["camera_name"])
            if not cam:
                return json.dumps({"error": "Camera not found."})
            index_code = (cam.get("cameraIndexCode") or
                          cam.get("deviceIndexCode") or
                          cam.get("deviceSerial", ""))
            data = self._authed_get(
                f"/v3/video/cameras/{index_code}/protocols",
                {"streamType": 0, "protocol": "hls", "transType": 1},
            )
            url = (data.get("url") or (data.get("data") or {}).get("url") or "")
            return json.dumps({
                "camera":       cam.get("cameraName", ""),
                "stream_url":   url,
                "instructions": "Open in VLC: Media → Open Network Stream → paste the URL",
            })
        except Exception as exc:
            return json.dumps({"error": str(exc)})
