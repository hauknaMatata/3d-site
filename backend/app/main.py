"""FastAPI application entrypoint."""
from __future__ import annotations

import asyncio
import contextlib
import json
import secrets

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import config, db, jobs
from .hub import hub
from .mcp_server import mcp
from .routes import projects, settings, team
from .runtime import get_runtime

mcp_app = mcp.streamable_http_app()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="AI 3D Studio API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def auth(request: Request, call_next):
    if config.API_TOKEN and request.method != "OPTIONS" and request.url.path not in ("/api/health",):
        tok = request.headers.get("authorization", "").removeprefix("Bearer ").strip() or request.query_params.get("token", "")
        if not secrets.compare_digest(tok, config.API_TOKEN):
            return JSONResponse({"detail": "Unauthorized"}, 401)
    return await call_next(request)


app.include_router(settings.router)
app.include_router(projects.router)
app.include_router(team.router)
app.mount("/mcp", mcp_app)


@app.websocket("/api/ws/{pid}")
async def ws(websocket: WebSocket, pid: str):
    if config.API_TOKEN and not secrets.compare_digest(websocket.query_params.get("token", ""), config.API_TOKEN):
        await websocket.close(code=4401)
        return
    if not db.q1("SELECT id FROM projects WHERE id=?", (pid,)):
        await websocket.close(code=4404)
        return
    await websocket.accept()
    q = hub.subscribe(pid)
    try:
        rt = get_runtime(pid)
        snap = await asyncio.to_thread(rt.snapshot)
        job = jobs.JOBS.get(pid)
        await websocket.send_json({"type": "hello", "snapshot": snap, "job": job.public() if job else None,
                                   "confirms": [{"id": k, "title": v["title"], "detail": v["detail"]}
                                                for k, v in hub.confirms.items() if v["project_id"] == pid]})

        async def pump():
            while True:
                ev = await q.get()
                await websocket.send_text(json.dumps(ev, default=str))

        async def recv():
            while True:
                msg = await websocket.receive_json()
                if msg.get("type") == "confirm":
                    hub.answer(str(msg.get("id")), bool(msg.get("approve")))
                elif msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})

        done, pending = await asyncio.wait([asyncio.create_task(pump()), asyncio.create_task(recv())],
                                           return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(pid, q)
