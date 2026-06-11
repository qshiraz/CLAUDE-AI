#!/usr/bin/env python3
"""Jarvis Web Dashboard — FastAPI server."""

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from jarvis.core.config import load_config
from jarvis.core.agent_registry import AgentRegistry
from jarvis.core.orchestrator import JarvisOrchestrator

cfg: dict = {}
registry: AgentRegistry | None = None
orchestrator: JarvisOrchestrator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global cfg, registry, orchestrator
    cfg = load_config(ROOT)
    registry = AgentRegistry()
    registry.load_builtin_agents(cfg)
    plugins_cfg = cfg.get("plugins", {})
    if plugins_cfg.get("enabled", True) and plugins_cfg.get("autoload", True):
        plugin_dir = ROOT / plugins_cfg.get("directory", "jarvis/plugins")
        registry.load_plugins(plugin_dir)
    orchestrator = JarvisOrchestrator(cfg, registry)
    _port = int(os.getenv("PORT", 8080))
    print(f"\n  Sahil Dashboard → http://localhost:{_port}\n")
    yield


app = FastAPI(lifespan=lifespan)


# ── Dashboard HTML ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    html_path = ROOT / "jarvis" / "web" / "dashboard.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


# ── Agent REST endpoints ──────────────────────────────────────────────────────

async def _dispatch(tool: str, params: dict) -> dict:
    result = await asyncio.to_thread(registry.dispatch, tool, params)
    try:
        return json.loads(result)
    except Exception:
        return {"error": result}


@app.get("/api/weather")
async def api_weather():
    return JSONResponse(await _dispatch("get_current_weather", {}))


@app.get("/api/forecast")
async def api_forecast():
    return JSONResponse(await _dispatch("get_weather_forecast", {"days": 3}))


@app.get("/api/news")
async def api_news():
    return JSONResponse(await _dispatch("get_local_news", {"count": 8}))


@app.get("/api/international")
async def api_international():
    return JSONResponse(await _dispatch("get_international_news", {"count": 5}))


@app.get("/api/flights")
async def api_flights():
    return JSONResponse(await _dispatch("get_nearby_flights", {"limit": 8}))


@app.get("/api/traffic")
async def api_traffic():
    return JSONResponse(await _dispatch("get_traffic_conditions", {}))


@app.get("/api/home")
async def api_home():
    return JSONResponse(await _dispatch("list_devices", {}))


@app.get("/api/email")
async def api_email():
    return JSONResponse(await _dispatch("check_inbox", {"count": 5, "unread_only": True}))


@app.get("/api/system")
async def api_system():
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.1)
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        net = psutil.net_io_counters()
        return JSONResponse({
            "cpu_percent": cpu,
            "ram_percent": ram.percent,
            "ram_used_gb": round(ram.used / 1e9, 1),
            "ram_total_gb": round(ram.total / 1e9, 1),
            "disk_percent": disk.percent,
            "disk_free_gb": round(disk.free / 1e9, 1),
            "net_sent_mb": round(net.bytes_sent / 1e6, 1),
            "net_recv_mb": round(net.bytes_recv / 1e6, 1),
        })
    except Exception:
        return JSONResponse({"cpu_percent": 0, "ram_percent": 0})


@app.get("/api/status")
async def api_status():
    return JSONResponse({
        "agents": registry.agent_names(),
        "location": cfg.get("jarvis", {}).get("location", {}),
        "user": cfg.get("jarvis", {}).get("user_name", "Boss"),
    })


# ── Memory endpoints ──────────────────────────────────────────────────────────

@app.get("/api/memory")
async def api_memory():
    from jarvis.core.memory import MemoryManager
    mem = MemoryManager()
    memories = mem.recall(limit=100)
    grouped: dict[str, list[dict]] = {}
    for m in memories:
        grouped.setdefault(m["category"], []).append(m)
    return JSONResponse({"memories": grouped, "total": len(memories)})


@app.post("/api/memory")
async def api_memory_add(request: Request):
    data = await request.json()
    from jarvis.core.memory import MemoryManager
    mem = MemoryManager()
    mem.remember(
        data.get("category", "general"),
        data.get("key", ""),
        data.get("value", ""),
        int(data.get("importance", 1)),
    )
    return JSONResponse({"remembered": True})


@app.delete("/api/memory/{category}/{key}")
async def api_memory_forget(category: str, key: str):
    from jarvis.core.memory import MemoryManager
    mem = MemoryManager()
    mem.forget(category, key)
    return JSONResponse({"forgotten": True})


# ── Education / student endpoints ─────────────────────────────────────────────

@app.get("/api/students")
async def api_students():
    from jarvis.core.memory import MemoryManager
    mem = MemoryManager()
    return JSONResponse({"students": mem.student_summary()})


@app.get("/api/students/{student_name}")
async def api_student_progress(student_name: str):
    from jarvis.core.memory import MemoryManager
    mem = MemoryManager()
    progress = mem.get_student_progress(student_name)
    return JSONResponse({"student": student_name, "sessions": len(progress), "progress": progress})


# ── Camera endpoints ──────────────────────────────────────────────────────────

@app.get("/api/cameras")
async def api_cameras():
    return JSONResponse(await _dispatch("list_cameras", {}))


@app.get("/api/cameras/stream/{channel}")
async def api_camera_stream(channel: int):
    """Proxy live MJPEG stream from DVR — works as <img> src in browser."""
    from fastapi.responses import StreamingResponse
    cam_cfg = cfg.get("camera", {})
    ip   = cam_cfg.get("dvr_ip", "")
    user = cam_cfg.get("dvr_username", "admin")
    pwd  = cam_cfg.get("dvr_password", "")
    if not ip or not pwd:
        return JSONResponse({"error": "DVR not configured"}, status_code=503)

    stream_urls = [
        f"http://{ip}/ISAPI/Streaming/channels/{channel}01/httpPreview",
        f"http://{ip}/cgi-bin/mjpg/video.cgi?channel={channel}&subtype=0",
        f"http://{ip}/video.cgi?channel={channel}",
    ]

    import httpx

    async def generate():
        async with httpx.AsyncClient(timeout=None) as client:
            for url in stream_urls:
                try:
                    async with client.stream("GET", url,
                                             auth=(user, pwd)) as resp:
                        if resp.status_code == 200:
                            async for chunk in resp.aiter_bytes(4096):
                                yield chunk
                            return
                except Exception:
                    continue

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=--myboundary",
        headers={"Cache-Control": "no-cache"},
    )


# Cache: channel -> (url, auth) that worked last time
_snap_cache: dict[int, tuple] = {}

@app.get("/api/cameras/snapshot/{channel}")
async def api_camera_snapshot(channel: int):
    """Proxy: fetch snapshot from DVR, compress, and return as JPEG."""
    cam_cfg = cfg.get("camera", {})
    ip   = cam_cfg.get("dvr_ip", "")
    user = cam_cfg.get("dvr_username", "admin")
    pwd  = cam_cfg.get("dvr_password", "")
    if not ip or not pwd:
        return JSONResponse({"error": "DVR not configured"}, status_code=503)
    import httpx
    import io
    from fastapi.responses import Response
    urls = [
        f"http://{ip}/ISAPI/Streaming/channels/{channel}01/picture",
        f"http://{ip}/cgi-bin/snapshot.cgi?channel={channel}",
    ]
    auth_methods = [httpx.DigestAuth(user, pwd), (user, pwd)]
    raw = None

    async with httpx.AsyncClient(timeout=5) as client:
        # Try cached combo first — instant on repeat calls
        if channel in _snap_cache:
            cached_url, cached_auth = _snap_cache[channel]
            try:
                r = await client.get(cached_url, auth=cached_auth)
                if r.status_code == 200 and len(r.content) > 500:
                    raw = r.content
            except Exception:
                del _snap_cache[channel]

        if not raw:
            for url in urls:
                for auth in auth_methods:
                    try:
                        r = await client.get(url, auth=auth)
                        if r.status_code == 200 and len(r.content) > 500:
                            raw = r.content
                            _snap_cache[channel] = (url, auth)
                            break
                    except Exception:
                        continue
                if raw:
                    break
    if not raw:
        return JSONResponse({"error": "Snapshot unavailable"}, status_code=503)
    # Compress: resize to max 800px wide, quality 60 → typically 15-40 KB
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        max_w = 800
        if img.width > max_w:
            h = int(img.height * max_w / img.width)
            img = img.resize((max_w, h), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=60, optimize=True)
        raw = buf.getvalue()
    except Exception:
        pass  # fall back to original if Pillow unavailable
    return Response(content=raw, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store", "X-Frame-Options": "SAMEORIGIN"})


@app.get("/api/guard-vision/cameras")
async def api_gv_cameras():
    return JSONResponse(await _dispatch("gv_list_cameras", {}))


@app.get("/api/guard-vision/debug")
async def api_gv_debug():
    """Try every known Hikvision share API path and show raw responses."""
    import httpx
    qr_id = cfg.get("guard_vision_share", {}).get("qr_id", "")
    bases = ["https://api.hik-connect.com", "https://apiisa.hik-connect.com"]
    paths = [
        "/v3/share/device/group/cameras",
        "/v3/share/devicegroup/cameras",
        "/v3/share/cameras",
        "/v3/userdevices/v1/share/cameras",
    ]
    results = []
    async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
        for base in bases:
            for path in paths:
                url = f"{base}{path}"
                try:
                    r = await client.get(url, params={"qrId": qr_id},
                                         headers={"clientType": "55", "lang": "en-US"})
                    try:
                        body = r.json()
                    except Exception:
                        body = r.text[:200]
                    results.append({"url": url, "status": r.status_code, "body": body})
                except Exception as exc:
                    results.append({"url": url, "error": str(exc)})
    return JSONResponse({"qr_id": qr_id, "results": results})


@app.get("/api/guard-vision/test")
async def api_gv_test():
    """Test which Hikvision API server is reachable from this machine."""
    import httpx
    endpoints = [
        "https://api.hik-connect.com",
        "https://apiisa.hik-connect.com",
        "https://apieur.hik-connect.com",
        "https://apiusa.hik-connect.com",
        "https://apicn.hik-connect.com",
    ]
    results = {}
    async with httpx.AsyncClient(timeout=6) as client:
        for ep in endpoints:
            try:
                r = await client.get(ep)
                results[ep] = f"OK ({r.status_code})"
            except Exception as exc:
                results[ep] = f"FAIL: {type(exc).__name__}"
    working = [k for k, v in results.items() if v.startswith("OK")]
    return JSONResponse({"results": results, "recommended": working[0] if working else "none"})


@app.get("/api/tuya")
async def api_tuya():
    return JSONResponse(await _dispatch("tuya_list_devices", {}))


@app.post("/api/tuya/control")
async def api_tuya_control(request: Request):
    data = await request.json()
    return JSONResponse(await _dispatch("tuya_control_device", {
        "device_id":   data.get("device_id", ""),
        "device_name": data.get("device_name", ""),
        "command":     data.get("command", "toggle"),
        "value":       data.get("value"),
    }))


@app.get("/api/worldcup")
async def api_worldcup():
    return JSONResponse(await _dispatch("get_worldcup_fixtures", {"days_ahead": 14}))


@app.get("/api/cameras/alerts")
async def api_camera_alerts():
    return JSONResponse(await _dispatch("get_motion_alerts", {"limit": 20}))


@app.post("/api/cameras/alert")
async def api_camera_alert(request: Request):
    """Webhook endpoint for Guard Vision / camera systems to push motion events."""
    data = await request.json()
    from jarvis.agents.camera_agent import add_motion_alert
    add_motion_alert(
        camera_id=data.get("camera_id", "unknown"),
        camera_name=data.get("camera_name", "Camera"),
        description=data.get("description", "Motion detected"),
    )
    return JSONResponse({"received": True})


@app.get("/api/radio/stream")
async def api_radio_stream(url: str):
    """Proxy radio stream to avoid CORS issues. Detects actual content-type."""
    import httpx
    from fastapi.responses import StreamingResponse

    # Quick HEAD-like request to detect content-type before streaming
    detected_mt = ["audio/mpeg"]
    u_lower = url.lower()
    if ".aac" in u_lower: detected_mt[0] = "audio/aac"
    elif ".ogg" in u_lower: detected_mt[0] = "audio/ogg"
    elif ".m3u8" in u_lower: detected_mt[0] = "application/vnd.apple.mpegurl"
    elif ".opus" in u_lower: detected_mt[0] = "audio/opus"
    elif ".flac" in u_lower: detected_mt[0] = "audio/flac"

    async def generate():
        async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
            try:
                async with client.stream(
                    "GET", url,
                    headers={"User-Agent": "Mozilla/5.0 SahilAI/1.0", "Icy-MetaData": "1"}
                ) as resp:
                    ct = resp.headers.get("content-type", "")
                    if ct:
                        base = ct.split(";")[0].strip()
                        if base and ("audio" in base or "mpegurl" in base or "octet" in base):
                            detected_mt[0] = base
                    async for chunk in resp.aiter_bytes(8192):
                        yield chunk
            except Exception:
                return

    return StreamingResponse(
        generate(),
        media_type=detected_mt[0],
        headers={
            "Cache-Control": "no-cache",
            "Access-Control-Allow-Origin": "*",
            "X-Content-Type-Options": "nosniff",
        },
    )


# ── Chat endpoints ────────────────────────────────────────────────────────────

@app.websocket("/ws/chat")
async def chat_ws(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            data = await ws.receive_json()
            message = data.get("message", "").strip()
            if not message:
                continue
            try:
                async for chunk in orchestrator.chat(message):
                    if chunk.startswith('\x00ACTION:') and chunk.endswith('\x00'):
                        try:
                            action = json.loads(chunk[8:-1])
                            await ws.send_json({"type": "action", **action})
                        except Exception:
                            pass
                    else:
                        await ws.send_json({"type": "chunk", "text": chunk})
                await ws.send_json({"type": "done"})
            except Exception as exc:
                await ws.send_json({"type": "error", "message": str(exc)})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


@app.post("/api/chat")
async def api_chat(request: Request):
    """Fallback non-streaming chat endpoint."""
    data = await request.json()
    message = data.get("message", "").strip()
    if not message:
        return JSONResponse({"error": "empty message"})
    parts: list[str] = []
    async for chunk in orchestrator.chat(message):
        parts.append(chunk)
    return JSONResponse({"response": "".join(parts)})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("\n  ERROR: ANTHROPIC_API_KEY not set.\n  Run: set ANTHROPIC_API_KEY=sk-ant-...\n")
        sys.exit(1)
    port = int(os.getenv("PORT", 8080))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
