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
    print(f"\n  Jarvis Web Dashboard → http://localhost:8080\n")
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


@app.get("/api/status")
async def api_status():
    return JSONResponse({
        "agents": registry.agent_names(),
        "location": cfg.get("jarvis", {}).get("location", {}),
        "user": cfg.get("jarvis", {}).get("user_name", "Boss"),
    })


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
    uvicorn.run("server:app", host="0.0.0.0", port=8080, reload=False)
