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


# ============================================================================ AI TEAM over MCP (no API keys)
AGENT_LOOP = ("You are now a member of the AI 3D Studio team. Repeat until you get status 'stop': call team_wait_for_task; "
              "if status is 'task', follow 'role_instructions' and 'task' exactly and answer as 'how_to_answer' says "
              "(modeling tasks: team_run_operation for every change, then team_submit); if status is 'no_task', call "
              "team_wait_for_task again. Be independent and critical; never invent measurements.")


def _mcp_member(agent: str) -> dict | None:
    from .team import config as tcfg

    a = (agent or "").strip().lower()
    for m in tcfg.load()["members"]:
        if m.get("kind") == "mcp" and m["enabled"] and (m["id"] == agent or (m.get("label") or "").strip().lower() == a):
            return m
    return None


@mcp.tool()
def team_join(agent_name: str) -> str:
    """Join the AI 3D Studio team as the MCP agent with this name (configured in the web UI → AI Team, member type
    'Agent MCP'). Returns your agent_id and the working loop to follow."""
    from .team import config as tcfg
    from .team.bridge import broker

    m = _mcp_member(agent_name)
    if not m:
        names = [x["label"] for x in tcfg.load()["members"] if x.get("kind") == "mcp" and x["enabled"]]
        return json.dumps({"error": f"No enabled MCP team member named '{agent_name}'.", "available_names": names,
                           "hint": "Add an 'Agent MCP' member with this name in the web UI → AI Team → ⚙."})
    broker.touch(m["id"])
    roles = tcfg.assign_roles(tcfg.load()["members"]).get(m["id"], [])
    return json.dumps({"agent_id": m["id"], "name": m["label"], "roles": [tcfg.ROLES[r]["label"] for r in roles],
                       "instructions": AGENT_LOOP})


@mcp.tool()
async def team_wait_for_task(agent_id: str, timeout_s: int = 40) -> str:
    """Wait (long-poll, max ~50 s) for your next team task. Call this in a loop. Returns status task | no_task."""
    from .team.bridge import broker

    m = _mcp_member(agent_id)
    if not m:
        return json.dumps({"status": "stop", "reason": "unknown or disabled agent_id — call team_join again"})
    t = await broker.next_task(m["id"], max(1, min(int(timeout_s or 40), 50)))
    if not t:
        return json.dumps({"status": "no_task", "hint": "No work right now. Call team_wait_for_task again."})
    return json.dumps(t.payload(), ensure_ascii=False)


@mcp.tool()
async def team_run_operation(task_id: str, tool: str, args: dict) -> str:
    """Execute ONE modeling operation for a 'build' task (validated, authored with your name, undoable).
    Use set_parameters with {"updates": {...}} for user parameters."""
    from .team.bridge import broker

    t = broker.tasks.get(task_id)
    if not t or t.done or t.kind != "build" or not t.run_op:
        return "ERROR: not an open build task_id"
    broker.touch(t.member_id)
    try:
        out = await t.run_op(tool, args or {})
        return json.dumps(out, default=str)[:8000]
    except EngineError as e:
        return f"ERROR: {e}"


@mcp.tool()
def team_submit(task_id: str, result: str) -> str:
    """Hand in the result of a team task (JSON object for 'json' tasks, text summary otherwise)."""
    from .team.bridge import broker

    ok, msg = broker.submit(task_id, result if isinstance(result, str) else json.dumps(result))
    return json.dumps({"ok": ok, "message": msg, "next": "call team_wait_for_task" if ok else "fix and submit again"})
