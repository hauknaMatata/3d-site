"""Projects, operations, attachments, versions, export, live-build jobs."""
from __future__ import annotations

import asyncio
import io
import json
import mimetypes
import shutil
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from .. import config, db, jobs
from ..engines.base import EngineError
from ..engines.imaging import dominant_colors, load_rgba
from ..export import exporters
from ..hub import hub
from ..runtime import ENGINES, drop_runtime, empty_state, get_runtime, project_dir

router = APIRouter(prefix="/api")

IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/svg+xml": ".svg"}
MODEL_EXT = {".glb", ".gltf", ".obj", ".stl", ".ply", ".step", ".stp", ".fbx", ".3mf"}


def _project(pid: str) -> dict:
    row = db.q1("SELECT id,name,mode,created,updated FROM projects WHERE id=?", (pid,))
    if not row:
        raise HTTPException(404, "Project not found")
    return row


def _rt(pid: str):
    _project(pid)
    return get_runtime(pid)


async def _snapshot(pid: str) -> dict:
    rt = get_runtime(pid)
    snap = await asyncio.to_thread(rt.snapshot)
    hub.publish(pid, {"type": "snapshot", "snapshot": snap})
    return snap


def _err(e: Exception) -> HTTPException:
    return HTTPException(400, str(e))


# ------------------------------------------------------------------------------------------ projects
class NewProject(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    mode: str


@router.get("/projects")
def list_projects():
    rows = db.q("SELECT id,name,mode,created,updated,state FROM projects ORDER BY updated DESC")
    out = []
    for r in rows:
        st = json.loads(r.pop("state"))
        r["ops"] = len(st.get("ops", []))
        out.append(r)
    return out


@router.post("/projects")
def create_project(body: NewProject):
    if body.mode not in ENGINES:
        raise HTTPException(400, "mode must be 'cad' or 'game'")
    pid = db.new_id()
    with db.tx() as c:
        c.execute("INSERT INTO projects(id,name,mode,created,updated,state) VALUES(?,?,?,?,?,?)",
                  (pid, body.name.strip(), body.mode, db.now(), db.now(), json.dumps(empty_state())))
    return _project(pid)


@router.get("/projects/{pid}")
async def get_project(pid: str):
    p = _project(pid)
    rt = get_runtime(pid)
    async with rt.lock:
        snap = await asyncio.to_thread(rt.snapshot)
    job = jobs.JOBS.get(pid)
    return {**p, "snapshot": snap, "job": job.public() if job else None}


class Rename(BaseModel):
    name: str = Field(min_length=1, max_length=120)


@router.patch("/projects/{pid}")
def rename_project(pid: str, body: Rename):
    _project(pid)
    with db.tx() as c:
        c.execute("UPDATE projects SET name=?, updated=? WHERE id=?", (body.name.strip(), db.now(), pid))
    rt = get_runtime(pid)
    rt.name = body.name.strip()
    return _project(pid)


@router.delete("/projects/{pid}")
def delete_project(pid: str, confirm: bool = False):
    _project(pid)
    if not confirm:
        raise HTTPException(409, "Deleting a project is irreversible — repeat with ?confirm=true after asking the user.")
    with db.tx() as c:
        for t in ("projects",):
            c.execute(f"DELETE FROM {t} WHERE id=?", (pid,))
        for t in ("versions", "attachments", "messages"):
            c.execute(f"DELETE FROM {t} WHERE project_id=?", (pid,))
    drop_runtime(pid)
    shutil.rmtree(config.FILES_DIR / pid, ignore_errors=True)
    return {"deleted": pid}


@router.post("/projects/{pid}/duplicate")
def duplicate_project(pid: str):
    src = db.q1("SELECT * FROM projects WHERE id=?", (pid,))
    if not src:
        raise HTTPException(404)
    nid = db.new_id()
    st = json.loads(src["state"])
    # copy attachments (ids are kept so operations still resolve)
    with db.tx() as c:
        c.execute("INSERT INTO projects(id,name,mode,created,updated,state) VALUES(?,?,?,?,?,?)",
                  (nid, src["name"] + " (kopia)", src["mode"], db.now(), db.now(), json.dumps({**st, "rev": 0})))
        for a in db.q("SELECT * FROM attachments WHERE project_id=?", (pid,)):
            newp = project_dir(nid) / Path(a["path"]).name
            shutil.copy(a["path"], newp)
            c.execute("INSERT INTO attachments VALUES(?,?,?,?,?,?,?,?,?)",
                      (a["id"] + "", nid, a["filename"], a["mime"], str(newp), a["width"], a["height"], a["size"], db.now()))
    return _project(nid)


@router.get("/projects/{pid}/model/{rev}.glb")
def model_glb(pid: str, rev: int):
    f = config.RENDER_DIR / pid / f"{rev}.glb"
    if not f.exists():
        rt = _rt(pid)
        rt.render_glb()
        f = config.RENDER_DIR / pid / f"{rt.state['rev']}.glb"
    if not f.exists() or f.stat().st_size == 0:
        raise HTTPException(404, "No model")
    return FileResponse(f, media_type="model/gltf-binary", headers={"Cache-Control": "public, max-age=31536000, immutable"})


# ------------------------------------------------------------------------------------------ operations
class OpIn(BaseModel):
    tool: str
    args: dict[str, Any] = {}
    label: str = ""


@router.post("/projects/{pid}/ops")
async def apply_op(pid: str, body: OpIn):
    rt = _rt(pid)
    async with rt.lock:
        try:
            r = await asyncio.to_thread(rt.apply, body.tool, body.args, body.label, "user")
        except EngineError as e:
            raise _err(e) from e
    if r.get("query"):
        return {"result": r["result"]}
    return {"result": r["result"], "op": r["op"], "snapshot": await _snapshot(pid)}


@router.post("/projects/{pid}/query")
async def query(pid: str, body: OpIn):
    rt = _rt(pid)
    try:
        return {"result": await asyncio.to_thread(rt.engine.query, body.tool, body.args)}
    except EngineError as e:
        raise _err(e) from e


@router.post("/projects/{pid}/undo")
async def undo(pid: str):
    rt = _rt(pid)
    async with rt.lock:
        op = await asyncio.to_thread(rt.undo)
    return {"undone": op, "snapshot": await _snapshot(pid)}


@router.post("/projects/{pid}/redo")
async def redo(pid: str):
    rt = _rt(pid)
    async with rt.lock:
        op = await asyncio.to_thread(rt.redo)
    return {"redone": op, "snapshot": await _snapshot(pid)}


class OpEdit(BaseModel):
    args: dict[str, Any]


@router.patch("/projects/{pid}/ops/{op_id}")
async def edit_op(pid: str, op_id: str, body: OpEdit):
    rt = _rt(pid)
    async with rt.lock:
        try:
            op = await asyncio.to_thread(rt.update_op, op_id, body.args, False)
        except EngineError as e:
            raise _err(e) from e
    return {"op": op, "snapshot": await _snapshot(pid)}


@router.delete("/projects/{pid}/ops/{op_id}")
async def delete_op(pid: str, op_id: str):
    rt = _rt(pid)
    async with rt.lock:
        try:
            op = await asyncio.to_thread(rt.delete_op, op_id)
        except EngineError as e:
            raise _err(e) from e
    return {"op": op, "snapshot": await _snapshot(pid)}


class ParamsIn(BaseModel):
    updates: dict[str, Any] = {}
    remove: list[str] = []


@router.post("/projects/{pid}/params")
async def set_params(pid: str, body: ParamsIn):
    rt = _rt(pid)
    async with rt.lock:
        try:
            await asyncio.to_thread(rt.set_params, body.updates, body.remove)
        except EngineError as e:
            raise _err(e) from e
    return {"snapshot": await _snapshot(pid)}


class Flags(BaseModel):
    hidden: bool | None = None
    locked: bool | None = None


@router.post("/projects/{pid}/objects/{name}/flags")
async def set_flags(pid: str, name: str, body: Flags):
    rt = _rt(pid)
    async with rt.lock:
        await asyncio.to_thread(rt.set_flags, name, body.hidden, body.locked)
    return {"snapshot": await _snapshot(pid)}


# ------------------------------------------------------------------------------------------ versions
class VersionIn(BaseModel):
    label: str = ""


@router.get("/projects/{pid}/versions")
def list_versions(pid: str):
    _project(pid)
    rows = db.q("SELECT id,created,label,state FROM versions WHERE project_id=? ORDER BY created DESC", (pid,))
    for r in rows:
        r["ops"] = len(json.loads(r.pop("state")).get("ops", []))
    return rows


def save_version(pid: str, label: str) -> dict:
    rt = get_runtime(pid)
    vid = db.new_id()
    with db.tx() as c:
        c.execute("INSERT INTO versions VALUES(?,?,?,?,?)", (vid, pid, db.now(), label or "Wersja", json.dumps(rt.state)))
    return {"id": vid, "label": label}


@router.post("/projects/{pid}/versions")
def create_version(pid: str, body: VersionIn):
    _project(pid)
    return save_version(pid, body.label)


@router.post("/projects/{pid}/versions/{vid}/restore")
async def restore_version(pid: str, vid: str):
    v = db.q1("SELECT * FROM versions WHERE id=? AND project_id=?", (vid, pid))
    if not v:
        raise HTTPException(404)
    rt = _rt(pid)
    save_version(pid, "Auto: przed przywróceniem")
    async with rt.lock:
        await asyncio.to_thread(rt.restore, json.loads(v["state"]))
    return {"snapshot": await _snapshot(pid)}


# ------------------------------------------------------------------------------------------ messages
@router.get("/projects/{pid}/messages")
def messages(pid: str):
    _project(pid)
    rows = db.q("SELECT * FROM messages WHERE project_id=? ORDER BY created", (pid,))
    for r in rows:
        r["meta"] = json.loads(r["meta"] or "{}")
    return rows


# ------------------------------------------------------------------------------------------ attachments
def _att_public(a: dict) -> dict:
    return {k: a[k] for k in ("id", "filename", "mime", "width", "height", "size", "created")} | {
        "url": f"/api/projects/{a['project_id']}/attachments/{a['id']}/file"}


def attachments_for(pid: str, ids: list[str] | None = None) -> list[dict]:
    rows = db.q("SELECT * FROM attachments WHERE project_id=? ORDER BY created", (pid,))
    if ids is not None:
        rows = [r for r in rows if r["id"] in ids]
    out = []
    for r in rows:
        d = dict(r)
        if d["mime"].startswith("image/"):
            try:
                d["palette"] = dominant_colors(d["path"])
            except Exception:  # noqa: BLE001
                d["palette"] = []
        out.append(d)
    return out


@router.get("/projects/{pid}/attachments")
def list_attachments(pid: str):
    _project(pid)
    return [_att_public(a) for a in db.q("SELECT * FROM attachments WHERE project_id=? ORDER BY created", (pid,))]


async def _store_upload(pid: str, f: UploadFile, allow_models: bool) -> dict:
    data = await f.read()
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File too large (max {config.MAX_UPLOAD_MB} MB)")
    name = Path(f.filename or "file").name
    ext = Path(name).suffix.lower()
    mime = f.content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
    if ext in (".jpg", ".jpeg"):
        mime = "image/jpeg"
    elif ext == ".svg":
        mime = "image/svg+xml"
    if mime in IMAGE_TYPES:
        if ext == ".svg":
            if b"<script" in data.lower() or b"javascript:" in data.lower():
                raise HTTPException(400, "SVG with scripts is not allowed")
        else:
            try:
                from PIL import Image

                Image.open(io.BytesIO(data)).verify()
            except Exception:  # noqa: BLE001
                raise HTTPException(400, "Not a valid image") from None
    elif not (allow_models and ext in MODEL_EXT):
        raise HTTPException(400, "Allowed: PNG, JPG, JPEG, WEBP, SVG" + (" or 3D models" if allow_models else ""))
    aid = db.new_id()
    path = project_dir(pid) / f"{aid}{ext or IMAGE_TYPES.get(mime, '')}"
    path.write_bytes(data)
    w = h = None
    if mime in IMAGE_TYPES:
        try:
            img = load_rgba(str(path))
            w, h = img.size
        except Exception:  # noqa: BLE001
            path.unlink(missing_ok=True)
            raise HTTPException(400, "Could not read image") from None
    with db.tx() as c:
        c.execute("INSERT INTO attachments VALUES(?,?,?,?,?,?,?,?,?)", (aid, pid, name, mime, str(path), w, h, len(data), db.now()))
    return db.q1("SELECT * FROM attachments WHERE id=?", (aid,))


@router.post("/projects/{pid}/attachments")
async def upload(pid: str, files: list[UploadFile] = File(...)):
    _project(pid)
    out = [_att_public(await _store_upload(pid, f, False)) for f in files]
    hub.publish(pid, {"type": "attachments"})
    return out


@router.get("/projects/{pid}/attachments/{aid}/file")
def attachment_file(pid: str, aid: str):
    a = db.q1("SELECT * FROM attachments WHERE id=? AND project_id=?", (aid, pid))
    if not a:
        raise HTTPException(404)
    headers = {"Content-Security-Policy": "script-src 'none'; sandbox"}
    return FileResponse(a["path"], media_type=a["mime"], headers=headers)


@router.delete("/projects/{pid}/attachments/{aid}")
def delete_attachment(pid: str, aid: str):
    a = db.q1("SELECT * FROM attachments WHERE id=? AND project_id=?", (aid, pid))
    if not a:
        raise HTTPException(404)
    rt = get_runtime(pid)
    if aid in json.dumps(rt.state["ops"]):
        raise HTTPException(409, "This attachment is used by an operation in the model history (undo/delete it first).")
    Path(a["path"]).unlink(missing_ok=True)
    with db.tx() as c:
        c.execute("DELETE FROM attachments WHERE id=?", (aid,))
    hub.publish(pid, {"type": "attachments"})
    return {"deleted": aid}


@router.post("/projects/{pid}/import")
async def import_model(pid: str, file: UploadFile = File(...)):
    rt = _rt(pid)
    a = await _store_upload(pid, file, True)
    ext = Path(a["filename"]).suffix.lower()
    if rt.mode == "cad":
        if ext not in (".step", ".stp"):
            raise HTTPException(400, "CAD projects import STEP (exact B-Rep). Mesh formats go to GAME projects.")
        tool = "import_step"
    else:
        if ext in (".step", ".stp"):
            raise HTTPException(400, "STEP is a CAD format — import it into a CAD project.")
        if ext in (".fbx", ".3mf"):
            # convert with assimp to glb for trimesh
            from ..export.exporters import _assimp

            out = Path(a["path"]).with_suffix(".glb")
            await asyncio.to_thread(_assimp, "export", a["path"], str(out), "-fglb2")
            with db.tx() as c:
                c.execute("UPDATE attachments SET path=? WHERE id=?", (str(out), a["id"]))
        tool = "import_mesh"
    async with rt.lock:
        try:
            r = await asyncio.to_thread(rt.apply, tool, {"attachment_id": a["id"], "name": Path(a["filename"]).stem},
                                        f"Import {a['filename']}", "user")
        except EngineError as e:
            raise _err(e) from e
    return {"result": r["result"], "snapshot": await _snapshot(pid)}


# ------------------------------------------------------------------------------------------ export
class ExportIn(BaseModel):
    format: str
    quality: str = "normal"
    texture_res: int = 1024
    materials: bool = True
    textures: bool = True
    animation: bool = False
    selection: list[str] | None = None
    units: str | None = None
    scale: float = 1.0
    compression: bool = True
    include_lods: bool = True
    include_hidden: bool = False


@router.get("/export/formats")
def export_formats():
    return {"formats": exporters.FORMATS, "suggestions": exporters.SUGGESTIONS, "units": list(exporters.UNIT_M)}


@router.post("/projects/{pid}/export")
async def export(pid: str, body: ExportIn):
    rt = _rt(pid)
    if body.texture_res not in (256, 512, 1024, 2048, 4096):
        raise HTTPException(400, "texture_res must be 256–4096")
    async with rt.lock:
        try:
            res = await asyncio.to_thread(exporters.export, rt, body.model_dump())
        except EngineError as e:
            raise _err(e) from e
    res.pop("path")
    res["url"] = f"/api/exports/{res['token']}"
    return res


@router.get("/exports/{token:path}")
def download_export(token: str):
    p = (config.EXPORT_DIR / token).resolve()
    if not str(p).startswith(str(config.EXPORT_DIR.resolve())) or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, filename=p.name, media_type="application/octet-stream")


@router.get("/projects/{pid}/bundle")
def bundle(pid: str):
    src = db.q1("SELECT * FROM projects WHERE id=?", (pid,))
    if not src:
        raise HTTPException(404)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        atts = db.q("SELECT * FROM attachments WHERE project_id=?", (pid,))
        z.writestr("project.json", json.dumps({"format": "ai3dstudio-project/1", "name": src["name"], "mode": src["mode"],
                                               "state": json.loads(src["state"]),
                                               "attachments": [{k: a[k] for k in ("id", "filename", "mime", "width", "height", "size")}
                                                               | {"file": Path(a["path"]).name} for a in atts]}, indent=1))
        for a in atts:
            z.write(a["path"], "attachments/" + Path(a["path"]).name)
    name = "".join(ch for ch in src["name"] if ch.isalnum() or ch in "-_ ").strip() or "project"
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}.ai3d.zip"'})


@router.post("/projects/import-bundle")
async def import_bundle(file: UploadFile = File(...)):
    data = await file.read()
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        meta = json.loads(z.read("project.json"))
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "Not an AI 3D Studio project bundle") from None
    if meta.get("format") != "ai3dstudio-project/1" or meta.get("mode") not in ENGINES:
        raise HTTPException(400, "Unsupported bundle")
    pid = db.new_id()
    st = {**empty_state(), **meta["state"], "rev": 0}
    with db.tx() as c:
        c.execute("INSERT INTO projects(id,name,mode,created,updated,state) VALUES(?,?,?,?,?,?)",
                  (pid, meta["name"], meta["mode"], db.now(), db.now(), json.dumps(st)))
        for a in meta.get("attachments", []):
            fname = Path(a["file"]).name
            target = project_dir(pid) / fname
            target.write_bytes(z.read("attachments/" + fname))
            c.execute("INSERT INTO attachments VALUES(?,?,?,?,?,?,?,?,?)",
                      (a["id"], pid, a["filename"], a["mime"], str(target), a.get("width"), a.get("height"), a.get("size"), db.now()))
    return _project(pid)


# ------------------------------------------------------------------------------------------ live build jobs
class JobIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    attachment_ids: list[str] = []
    selected: str | None = None
    provider_id: str | None = None
    model: str | None = None


@router.post("/projects/{pid}/jobs")
async def start_job(pid: str, body: JobIn):
    _project(pid)
    atts = attachments_for(pid, body.attachment_ids)
    jobs.save_message(pid, "user", body.prompt, {"attachments": [a["id"] for a in atts], "selected": body.selected})
    prov = {"provider_id": body.provider_id, "model": body.model} if body.provider_id else None
    try:
        job = jobs.start(pid, body.prompt, atts, body.selected, prov)
    except EngineError as e:
        raise HTTPException(409, str(e)) from e
    return job.public()


@router.get("/projects/{pid}/jobs/current")
def current_job(pid: str):
    j = jobs.JOBS.get(pid)
    return j.public() if j else None


@router.post("/projects/{pid}/jobs/stop")
def stop_job(pid: str):
    j = jobs.stop(pid)
    return j.public() if j else None


@router.post("/projects/{pid}/jobs/resume")
def resume_job(pid: str):
    try:
        return jobs.resume(pid).public()
    except EngineError as e:
        raise HTTPException(409, str(e)) from e


class ConfirmIn(BaseModel):
    approve: bool


@router.post("/confirm/{cid}")
def confirm(cid: str, body: ConfirmIn):
    if not hub.answer(cid, body.approve):
        raise HTTPException(404, "No pending confirmation")
    return {"ok": True}
