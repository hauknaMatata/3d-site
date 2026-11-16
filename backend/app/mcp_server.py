"""This backend as an MCP server (Streamable HTTP at /mcp): external agents such as Claude Desktop/Code can
drive the same CAD/GAME engines. Destructive tools require approval by the user in the web UI."""
from __future__ import annotations

import asyncio
import json

from mcp.server.fastmcp import FastMCP

from . import db
from .engines.base import EngineError
from .hub import hub
from .runtime import ENGINES, get_runtime

mcp = FastMCP("ai-3d-studio", stateless_http=True, streamable_http_path="/")


@mcp.tool()
def list_projects() -> str:
    """List projects (id, name, mode cad|game)."""
    return json.dumps(db.q("SELECT id, name, mode, updated FROM projects ORDER BY updated DESC"))


@mcp.tool()
def list_modeling_tools(mode: str) -> str:
    """List modeling tools with JSON schemas for a mode ('cad' or 'game')."""
    if mode not in ENGINES:
        return "mode must be cad or game"
    return json.dumps([{"name": t.name, "description": t.description, "input_schema": t.schema()}
                       for t in ENGINES[mode].tools.values()])


@mcp.tool()
def create_project(name: str, mode: str) -> str:
    """Create a new project. mode = 'cad' (mm, B-Rep) or 'game' (m, PBR meshes)."""
    from .runtime import empty_state

    if mode not in ENGINES:
        return "mode must be cad or game"
    pid = db.new_id()
    with db.tx() as c:
        c.execute("INSERT INTO projects(id,name,mode,created,updated,state) VALUES(?,?,?,?,?,?)",
                  (pid, name, mode, db.now(), db.now(), json.dumps(empty_state())))
    return json.dumps({"id": pid, "name": name, "mode": mode})


@mcp.tool()
async def get_scene(project_id: str) -> str:
    """Objects, user parameters and operation history of a project."""
    rt = get_runtime(project_id)
    s = await asyncio.to_thread(rt.snapshot, False)
    return json.dumps({k: s[k] for k in ("objects", "params", "ops", "units", "mode")})


@mcp.tool()
async def run_operation(project_id: str, tool: str, args: dict) -> str:
    """Execute one modeling tool on a project (the web UI viewport updates live)."""
    rt = get_runtime(project_id)
    try:
        async with rt.lock:
            r = await asyncio.to_thread(rt.apply, tool, args, tool.replace("_", " "), "mcp")
            snap = await asyncio.to_thread(rt.snapshot)
    except EngineError as e:
        return f"ERROR: {e}"
    hub.publish(project_id, {"type": "snapshot", "snapshot": snap})
    return json.dumps(r.get("result"), default=str)


@mcp.tool()
async def set_parameters(project_id: str, updates: dict) -> str:
    """Set user parameters of a project (dependent '=expr' features regenerate)."""
    rt = get_runtime(project_id)
    try:
        async with rt.lock:
            p = await asyncio.to_thread(rt.set_params, updates)
            snap = await asyncio.to_thread(rt.snapshot)
    except EngineError as e:
        return f"ERROR: {e}"
    hub.publish(project_id, {"type": "snapshot", "snapshot": snap})
    return json.dumps(p)


@mcp.tool()
async def undo(project_id: str) -> str:
    """Undo the last operation."""
    rt = get_runtime(project_id)
    async with rt.lock:
        op = await asyncio.to_thread(rt.undo)
        snap = await asyncio.to_thread(rt.snapshot)
    hub.publish(project_id, {"type": "snapshot", "snapshot": snap})
    return json.dumps({"undone": op["label"] if op else None})


@mcp.tool()
async def export_model(project_id: str, format: str, units: str | None = None) -> str:
    """Export the model (stl, 3mf, obj, fbx, glb, gltf, step, ply, usdz); returns a download path + validation."""
    from .export import exporters

    rt = get_runtime(project_id)
    try:
        async with rt.lock:
            res = await asyncio.to_thread(exporters.export, rt, {"format": format, "units": units})
    except EngineError as e:
        return f"ERROR: {e}"
    res.pop("path")
    res["url"] = f"/api/exports/{res['token']}"
    return json.dumps(res)


@mcp.tool()
async def delete_project(project_id: str) -> str:
    """DANGEROUS: permanently delete a project. The user must approve it in the web UI."""
    row = db.q1("SELECT name FROM projects WHERE id=?", (project_id,))
    if not row:
        return "ERROR: project not found"
    ok = await hub.confirm(project_id, f"MCP: usunąć projekt „{row['name']}”?", "Operacja nieodwracalna (wywołanie z MCP).")
    if not ok:
        return "Declined by the user."
    from .routes.projects import delete_project as _del

    _del(project_id, confirm=True)
    return "deleted"
