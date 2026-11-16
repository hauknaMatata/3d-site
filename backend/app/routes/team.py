"""AI TEAM API: configuration, usage, team jobs, discussion, issues (approve / reject / revert), QA."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db, jobs
from ..engines.base import EngineError
from ..hub import hub
from ..qa import run_qa
from ..runtime import get_runtime
from ..team import config as tcfg
from ..team.orchestrator import WAITERS
from .projects import _project, _snapshot, attachments_for

router = APIRouter(prefix="/api")


@router.get("/team/config")
def get_config():
    cfg = tcfg.load()
    return {**cfg, "roles": tcfg.ROLES, "statuses": tcfg.STATUSES, "effective_roles": tcfg.assign_roles(cfg["members"])}


@router.put("/team/config")
def put_config(body: dict):
    try:
        cfg = tcfg.save({**tcfg.load(), **body})
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e)) from e
    return {**cfg, "roles": tcfg.ROLES, "statuses": tcfg.STATUSES, "effective_roles": tcfg.assign_roles(cfg["members"])}


@router.get("/team/mcp/agents")
def mcp_agents():
    from ..team.bridge import broker

    out = []
    for m in tcfg.load()["members"]:
        if m.get("kind") == "mcp":
            out.append({"id": m["id"], "label": m["label"], "enabled": m["enabled"], "connected": broker.connected(m["id"]),
                        "last_seen": broker.seen.get(m["id"]), "pending": [t.title for t in broker.pending_for(m["id"])]})
    return out


@router.get("/team/usage")
def usage():
    return db.q("SELECT * FROM agent_usage ORDER BY updated DESC")


class TeamJobIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    attachment_ids: list[str] = []
    selected: str | None = None
    mode: str | None = None
    member_id: str | None = None
    task: str | None = None


@router.post("/projects/{pid}/team/jobs")
async def start(pid: str, body: TeamJobIn):
    _project(pid)
    if body.mode and body.mode not in ("auto", "review", "manual"):
        raise HTTPException(400, "mode must be auto|review|manual")
    if body.task and body.task not in ("plan", "build", "review", "fix"):
        raise HTTPException(400, "task must be plan|build|review|fix")
    atts = attachments_for(pid, body.attachment_ids)
    jobs.save_message(pid, "user", body.prompt, {"attachments": [a["id"] for a in atts], "selected": body.selected, "team": True,
                                                  "mode": body.mode, "task": body.task})
    try:
        job = jobs.start_team(pid, body.prompt, atts, body.selected, body.mode, body.member_id, body.task)
    except EngineError as e:
        raise HTTPException(409, str(e)) from e
    return job.public()


@router.get("/projects/{pid}/team/messages")
def messages(pid: str, job_id: str | None = None, limit: int = 400):
    _project(pid)
    q = "SELECT * FROM team_messages WHERE project_id=?" + (" AND job_id=?" if job_id else "") + " ORDER BY created DESC LIMIT ?"
    rows = db.q(q, (pid, job_id, limit) if job_id else (pid, limit))
    for r in rows:
        r["data"] = json.loads(r["data"]) if r["data"] else None
    return list(reversed(rows))


def _issue(r: dict) -> dict:
    for k in ("fix", "reviews", "decision", "op_ids"):
        r[k] = json.loads(r[k]) if r[k] else None
    r["backed_by_test"] = bool(r["backed_by_test"])
    r["author"] = r.pop("author_name")
    return r


@router.get("/projects/{pid}/team/issues")
def issues(pid: str):
    _project(pid)
    return [_issue(r) for r in db.q("SELECT * FROM team_issues WHERE project_id=? ORDER BY created DESC LIMIT 300", (pid,))]


class Decide(BaseModel):
    approve: bool


@router.post("/projects/{pid}/team/issues/{iid}/decide")
def decide(pid: str, iid: str, body: Decide):
    r = db.q1("SELECT * FROM team_issues WHERE id=? AND project_id=?", (iid, pid))
    if not r:
        raise HTTPException(404)
    if r["status"] != "pending_user":
        raise HTTPException(409, f"Issue is '{r['status']}', not waiting for approval")
    status = "user_approved" if body.approve else "user_rejected"
    with db.tx() as c:
        c.execute("UPDATE team_issues SET status=?, updated=? WHERE id=?", (status, db.now(), iid))
    # keep the orchestrator's in-memory copy in sync
    job = jobs.JOBS.get(pid)
    if job and job.engine == "team":
        for i in job.team.get("issues", []):
            if i["id"] == iid:
                i["status"] = status
        ev = WAITERS.get(job.id)
        if ev:
            ev.set()
    out = _issue(db.q1("SELECT * FROM team_issues WHERE id=?", (iid,)))
    hub.publish(pid, {"type": "team_issue", "issue": out})
    return out


@router.post("/projects/{pid}/team/issues/{iid}/revert")
async def revert(pid: str, iid: str):
    r = db.q1("SELECT * FROM team_issues WHERE id=? AND project_id=?", (iid, pid))
    if not r or r["status"] != "applied":
        raise HTTPException(409, "Only applied fixes can be reverted")
    job = jobs.JOBS.get(pid)
    if job and job.status == "running":
        raise HTTPException(409, "Stop the running team job first")
    rt = get_runtime(pid)
    ops = json.loads(r["op_ids"] or "[]")
    undo = (json.loads(r["decision"] or "{}") or {}).get("undo") or {"kind": "ops"}
    async with rt.lock:
        try:
            if undo["kind"] == "params":
                await asyncio.to_thread(rt.set_params, {k: v for k, v in undo["prev"].items() if v is not None},
                                        [k for k, v in undo["prev"].items() if v is None])
            elif undo["kind"] == "op":
                await asyncio.to_thread(rt.update_op, undo["op_id"], undo["prev_args"], False)
            else:
                for oid in reversed(ops):
                    await asyncio.to_thread(rt.delete_op, oid)
        except EngineError as e:
            raise HTTPException(409, f"Revert failed: {e}") from e
    with db.tx() as c:
        c.execute("UPDATE team_issues SET status='reverted', updated=? WHERE id=?", (db.now(), iid))
    out = _issue(db.q1("SELECT * FROM team_issues WHERE id=?", (iid,)))
    hub.publish(pid, {"type": "team_issue", "issue": out})
    return {"issue": out, "snapshot": await _snapshot(pid)}


@router.get("/projects/{pid}/team/qa")
def qa_reports(pid: str, limit: int = 10):
    _project(pid)
    rows = db.q("SELECT * FROM team_qa WHERE project_id=? ORDER BY created DESC LIMIT ?", (pid, limit))
    for r in rows:
        r["report"] = json.loads(r["report"])
    return rows


@router.post("/projects/{pid}/team/qa")
async def run_qa_now(pid: str):
    """Technical tests only — no AI involved."""
    _project(pid)
    rt = get_runtime(pid)
    async with rt.lock:
        rep = await asyncio.to_thread(run_qa, rt)
    with db.tx() as c:
        c.execute("INSERT INTO team_qa VALUES(?,?,?,?,?,?,?)", (db.new_id(), pid, None, 0, "ręczna", json.dumps(rep, default=str), db.now()))
    hub.publish(pid, {"type": "team_qa", "report": rep, "stage": "ręczna", "round": 0})
    return rep
