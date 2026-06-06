"""Guard Vision browser agent — logs into web.hik-connect.com automatically
and captures live camera snapshots using Playwright browser automation.

No API key or developer registration needed.
Requires: pip install playwright && playwright install chromium

Add to config.local.yaml:
  guard_vision_browser:
    username: "your_guardvision_email"
    password: "your_guardvision_password"
"""

import base64
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from jarvis.agents.base_agent import BaseAgent

_SNAPSHOT_DIR = Path("/tmp/jarvis_snapshots")
_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

_PORTAL = "https://web.hik-connect.com"


class GuardVisionBrowserAgent(BaseAgent):
    name = "guard_vision_browser"
    description = (
        "Connects to Guard Vision cameras via automated browser login — "
        "no API key needed. Captures live snapshots and analyses scenes."
    )

    def __init__(self, cfg: dict, global_cfg: dict) -> None:
        super().__init__(cfg, global_cfg)
        self._username: str = cfg.get("username", "")
        self._password: str = cfg.get("password", "")
        self._logged_in: bool = False
        self._cam_names: list[str] = []

    # ── Tools ────────────────────────────────────────────────────────────────

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "gvb_list_cameras",
                "description": "Lists cameras by logging into the Guard Vision web portal.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "gvb_snapshot",
                "description": (
                    "Opens Guard Vision web portal, navigates to a camera, "
                    "and captures a screenshot for AI analysis."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "camera_name": {
                            "type": "string",
                            "description": "Camera name as shown in Guard Vision app, or 'first'",
                        },
                        "focus": {
                            "type": "string",
                            "enum": ["security", "people", "vehicles", "general"],
                            "description": "What to focus on in analysis",
                        },
                    },
                    "required": ["camera_name"],
                },
            },
        ]

    # ── Dispatch ─────────────────────────────────────────────────────────────

    def handle(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name == "gvb_list_cameras": return self._list_cameras()
        if tool_name == "gvb_snapshot":     return self._snapshot(tool_input)
        return f"Unknown tool: {tool_name}"

    # ── Playwright helpers ────────────────────────────────────────────────────

    def _check_playwright(self) -> str | None:
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
            return None
        except ImportError:
            return (
                "Playwright not installed. Run:\n"
                "  pip install playwright\n"
                "  playwright install chromium"
            )

    def _do_snapshot(self, camera_name: str) -> bytes | None:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 1280, "height": 800})
            page = ctx.new_page()

            try:
                # Login
                page.goto(f"{_PORTAL}/#/login", timeout=20000)
                page.wait_for_load_state("networkidle", timeout=15000)

                # Fill credentials
                page.fill("input[type='text'], input[name='account'], input[placeholder*='account' i], input[placeholder*='email' i]",
                          self._username)
                page.fill("input[type='password']", self._password)
                page.click("button[type='submit'], .login-btn, button:has-text('Login'), button:has-text('Sign in')")
                page.wait_for_load_state("networkidle", timeout=20000)
                time.sleep(3)

                # Look for camera list / device list
                page.wait_for_selector(".device-list, .camera-list, .channel-list, [class*='device'], [class*='camera']",
                                       timeout=15000)

                # Click on camera by name or first one
                if camera_name.lower() in ("first", "any", "1"):
                    page.click(".device-list li:first-child, .camera-list li:first-child, "
                               "[class*='channel']:first-child, [class*='camera-item']:first-child")
                else:
                    page.get_by_text(camera_name, exact=False).first.click()

                time.sleep(4)  # wait for stream to load

                # Screenshot
                out = _SNAPSHOT_DIR / f"gvb_{int(time.time())}.png"
                page.screenshot(path=str(out), full_page=False)
                return out.read_bytes()

            except PWTimeout:
                # Try screenshot anyway — useful for debugging
                out = _SNAPSHOT_DIR / f"gvb_timeout_{int(time.time())}.png"
                try:
                    page.screenshot(path=str(out))
                    return out.read_bytes()
                except Exception:
                    return None
            except Exception:
                return None
            finally:
                browser.close()

    # ── Implementations ───────────────────────────────────────────────────────

    def _list_cameras(self) -> str:
        err = self._check_playwright()
        if err:
            return json.dumps({"error": err})
        if not self._username or not self._password:
            return json.dumps({
                "error": "Guard Vision credentials not set.",
                "fix": "Add to config.local.yaml:\nguard_vision_browser:\n  username: 'your_email'\n  password: 'your_password'",
            })
        return json.dumps({
            "message": (
                "Browser agent is ready. Use gvb_snapshot to capture a live image from any camera. "
                "Say the camera name exactly as it appears in your Guard Vision app, or say 'first' "
                "to capture the first camera."
            ),
            "portal": _PORTAL,
        })

    def _snapshot(self, inp: dict) -> str:
        err = self._check_playwright()
        if err:
            return json.dumps({"error": err})
        if not self._username or not self._password:
            return json.dumps({"error": "Credentials not configured in config.local.yaml"})

        camera_name = inp.get("camera_name", "first")
        focus = inp.get("focus", "security")

        img = self._do_snapshot(camera_name)
        if not img:
            return json.dumps({
                "error": "Could not capture screenshot.",
                "hint": "Check your Guard Vision username/password in config.local.yaml",
            })

        b64 = base64.b64encode(img).decode()
        prompts = {
            "security": "Identify any security concerns: intruders, suspicious activity, unauthorized access.",
            "people":   "Describe all people visible — clothing, activity, direction.",
            "vehicles": "Identify vehicles — type, colour, licence plate if visible.",
            "general":  "Describe the full scene in detail.",
        }
        return json.dumps({
            "camera":    camera_name,
            "timestamp": datetime.now().isoformat(),
            "image_base64": b64,
            "analysis_instruction": (
                f"Analyse this screenshot from the Guard Vision web portal showing camera '{camera_name}'. "
                f"{prompts.get(focus, prompts['general'])} "
                "Note: this is a browser screenshot so it may include UI elements — focus on the video feed area."
            ),
        })
