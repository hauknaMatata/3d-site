"""AI provider manager, model/task assignment, MCP server manager, capabilities."""
from __future__ import annotations

import json
import shutil
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config, db, mcp_client, security
from ..ai import service
from ..ai.providers.adapters import REGISTRY, make
from ..ai.providers.base import ProviderError
from ..engines.game.textures import PRESETS
from ..export.exporters import FORMATS
from ..runtime import ENGINES

router = APIRouter(prefix="/api")


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/capabilities")
def capabilities():
    def ok(mod: str) -> bool:
        try:
            __import__(mod)
            return True
        except Exception:  # noqa: BLE001
            return False

    return {
        "modes": {m: {"tools": [{"name": t.name, "description": t.description, "schema": t.schema(), "mutating": t.mutating}
                                for t in e.tools.values()]} for m, e in ENGINES.items()},
        "engines": {"opencascade_cadquery": ok("cadquery"), "trimesh": ok("trimesh"), "manifold_boolean": ok("manifold3d"),
                    "lib3mf": ok("lib3mf"), "usd_core": ok("pxr"), "assimp_fbx": bool(shutil.which(config.ASSIMP_BIN)),
                    "mesh_simplification": ok("fast_simplification")},
        "formats": FORMATS, "material_presets": list(PRESETS),
        "provider_kinds": [{"kind": k, "label": c.label, "needs_key": c.needs_key, "default_base_url": c.default_base_url,
                            "suggested_models": c.suggested_models} for k, c in REGISTRY.items()],
        "tasks": service.TASKS, "auth": bool(config.API_TOKEN),
        "planned_modules": ["AI Retopology", "AI Rigging", "AI Animation", "AI Texture Generator (diffusion)",
                            "AI Image to 3D (external generator adapters)", "AI Scene Generator"],
    }


# ------------------------------------------------------------------------------------------ providers
class ProviderIn(BaseModel):
    kind: str
    name: str = ""
    api_key: str | None = None
    base_url: str = ""
    default_model: str = ""
    enabled: bool = True


@router.get("/providers")
def list_providers():
    return {"providers": [service.public_row(r) for r in db.q("SELECT * FROM providers ORDER BY created")],
            "default_provider": db.get_setting("default_provider"), "assignments": db.get_setting("assignments", {})}


@router.post("/providers")
def add_provider(body: ProviderIn):
    if body.kind not in REGISTRY:
        raise HTTPException(400, f"kind must be one of {list(REGISTRY)}")
    try:
        make(body.kind, body.api_key or "", body.base_url)
    except ProviderError as e:
        raise HTTPException(400, str(e)) from e
    pid = db.new_id()
    with db.tx() as c:
        c.execute("INSERT INTO providers VALUES(?,?,?,?,?,?,?,?,?)",
                  (pid, body.kind, body.name or REGISTRY[body.kind].label, body.base_url, security.encrypt(body.api_key or ""),
                   json.dumps(REGISTRY[body.kind].suggested_models), body.default_model or (REGISTRY[body.kind].suggested_models or [""])[0],
                   int(body.enabled), db.now()))
    if not db.get_setting("default_provider"):
        db.set_setting("default_provider", pid)
    return service.public_row(db.q1("SELECT * FROM providers WHERE id=?", (pid,)))


class ProviderPatch(BaseModel):
    name: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    default_model: str | None = None
    enabled: bool | None = None


@router.patch("/providers/{pid}")
def patch_provider(pid: str, body: ProviderPatch):
    r = db.q1("SELECT * FROM providers WHERE id=?", (pid,))
    if not r:
        raise HTTPException(404)
    upd: dict[str, Any] = {}
    if body.name is not None:
        upd["name"] = body.name
    if body.api_key:
        upd["api_key"] = security.encrypt(body.api_key)
    if body.base_url is not None:
        upd["base_url"] = body.base_url
    if body.default_model is not None:
        upd["default_model"] = body.default_model
    if body.enabled is not None:
        upd["enabled"] = int(body.enabled)
    if upd:
        with db.tx() as c:
            c.execute(f"UPDATE providers SET {', '.join(k + '=?' for k in upd)} WHERE id=?", (*upd.values(), pid))
    return service.public_row(db.q1("SELECT * FROM providers WHERE id=?", (pid,)))


@router.delete("/providers/{pid}")
def delete_provider(pid: str):
    with db.tx() as c:
        c.execute("DELETE FROM providers WHERE id=?", (pid,))
    if db.get_setting("default_provider") == pid:
        db.set_setting("default_provider", None)
    a = db.get_setting("assignments", {}) or {}
    db.set_setting("assignments", {k: v for k, v in a.items() if v.get("provider_id") != pid})
    return {"deleted": pid}


@router.post("/providers/{pid}/test")
async def test_provider(pid: str, model: str | None = None):
    try:
        p, r = service.instance(pid)
        res = await p.test(model or r["default_model"] or None)
    except ProviderError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001  (network errors)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if res.get("models"):
        with db.tx() as c:
            c.execute("UPDATE providers SET models=? WHERE id=?", (json.dumps(res["models"]), pid))
    return res


@router.get("/providers/{pid}/models")
async def provider_models(pid: str):
    try:
        p, _ = service.instance(pid)
        models = await p.list_models()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Could not list models: {e}") from e
    with db.tx() as c:
        c.execute("UPDATE providers SET models=? WHERE id=?", (json.dumps(models), pid))
    return models


class DefaultIn(BaseModel):
    provider_id: str | None


@router.put("/ai/default")
def set_default(body: DefaultIn):
    db.set_setting("default_provider", body.provider_id)
    return {"default_provider": body.provider_id}


@router.put("/ai/assignments")
def set_assignments(body: dict[str, dict[str, str | None]]):
    for k in body:
        if k not in service.TASKS:
            raise HTTPException(400, f"Unknown task {k}")
    db.set_setting("assignments", body)
    return body


# ------------------------------------------------------------------------------------------ MCP servers
class McpIn(BaseModel):
    name: str
    url: str
    transport: str = "streamable_http"
    headers: dict[str, str] = {}
    permission: str = "ask"
    enabled: bool = True


def _mcp_public(r: dict) -> dict:
    hdr = json.loads(security.decrypt(r["headers"]) or "{}") if r.get("headers") else {}
    return {"id": r["id"], "name": r["name"], "url": r["url"], "transport": r["transport"], "enabled": bool(r["enabled"]),
            "permission": r["permission"], "disabled_tools": json.loads(r["disabled_tools"] or "[]"),
            "tools": json.loads(r["tools_cache"] or "[]"), "status": r["status"],
            "header_names": list(hdr.keys())}


def _validate_mcp(body: McpIn) -> None:
    if not body.url.startswith(("https://", "http://")):
        raise HTTPException(400, "Only remote MCP servers over HTTP(S) are supported (stdio is disabled on a web backend).")
    if body.transport not in ("streamable_http", "sse"):
        raise HTTPException(400, "transport must be streamable_http or sse")
    if body.permission not in ("ask", "allow", "deny"):
        raise HTTPException(400, "permission must be ask|allow|deny")


@router.get("/mcp/servers")
def list_mcp():
    return [_mcp_public(r) for r in db.q("SELECT * FROM mcp_servers ORDER BY created")]


@router.post("/mcp/servers")
def add_mcp(body: McpIn):
    _validate_mcp(body)
    sid = db.new_id()
    with db.tx() as c:
        c.execute("INSERT INTO mcp_servers(id,name,url,transport,headers,enabled,permission,created) VALUES(?,?,?,?,?,?,?,?)",
                  (sid, body.name, body.url, body.transport, security.encrypt(json.dumps(body.headers)) if body.headers else "",
                   int(body.enabled), body.permission, db.now()))
    return _mcp_public(db.q1("SELECT * FROM mcp_servers WHERE id=?", (sid,)))


class McpPatch(BaseModel):
    enabled: bool | None = None
    permission: str | None = None
    disabled_tools: list[str] | None = None
    name: str | None = None


@router.patch("/mcp/servers/{sid}")
def patch_mcp(sid: str, body: McpPatch):
    r = db.q1("SELECT * FROM mcp_servers WHERE id=?", (sid,))
    if not r:
        raise HTTPException(404)
    if body.permission and body.permission not in ("ask", "allow", "deny"):
        raise HTTPException(400, "permission must be ask|allow|deny")
    upd: dict[str, Any] = {}
    if body.enabled is not None:
        upd["enabled"] = int(body.enabled)
    if body.permission:
        upd["permission"] = body.permission
    if body.disabled_tools is not None:
        upd["disabled_tools"] = json.dumps(body.disabled_tools)
    if body.name:
        upd["name"] = body.name
    if upd:
        with db.tx() as c:
            c.execute(f"UPDATE mcp_servers SET {', '.join(k + '=?' for k in upd)} WHERE id=?", (*upd.values(), sid))
    return _mcp_public(db.q1("SELECT * FROM mcp_servers WHERE id=?", (sid,)))


@router.delete("/mcp/servers/{sid}")
def delete_mcp(sid: str):
    with db.tx() as c:
        c.execute("DELETE FROM mcp_servers WHERE id=?", (sid,))
    return {"deleted": sid}


@router.post("/mcp/servers/{sid}/test")
async def test_mcp(sid: str):
    r = db.q1("SELECT * FROM mcp_servers WHERE id=?", (sid,))
    if not r:
        raise HTTPException(404)
    try:
        tools = await mcp_client.list_tools(r)
        status, err = "online", None
    except BaseException as e:  # noqa: BLE001  (anyio raises ExceptionGroups)
        tools, status, err = json.loads(r["tools_cache"] or "[]"), "error", f"{type(e).__name__}: {e}"[:400]
    with db.tx() as c:
        c.execute("UPDATE mcp_servers SET status=?, tools_cache=? WHERE id=?", (status, json.dumps(tools), sid))
    return {"status": status, "error": err, "server": _mcp_public(db.q1("SELECT * FROM mcp_servers WHERE id=?", (sid,)))}
