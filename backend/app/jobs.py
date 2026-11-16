"""LIVE BUILD jobs: every step is a real engine operation; after each one the viewport model is
re-rendered and pushed over WebSocket. Supports stop, resume after error, confirmations."""
from __future__ import annotations

import asyncio
import base64
import json
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any

from . import config, db, mcp_client
from .ai import service
from .ai.prompts import system_prompt
from .ai.providers.base import ProviderError, ToolDef
from .engines.base import EngineError
from .hub import hub
from .planner.local import plan as local_plan
from .runtime import get_runtime


@dataclass
class Job:
    id: str
    project_id: str
    prompt: str
    attachments: list[dict]
    selected: str | None
    engine: str  # "planner" | "llm"
    provider: dict | None = None
    status: str = "running"
    stage: str = ""
    stages: list[str] = field(default_factory=list)
    step_index: int = 0
    step_total: int = 0
    pending: list[dict] = field(default_factory=list)  # planner steps not yet executed
    convo: list[dict] = field(default_factory=list)  # LLM conversation for resume
    error: str = ""
    stop: bool = False
    task: asyncio.Task | None = None
    started: float = field(default_factory=time.time)
    log: list[dict] = field(default_factory=list)

    def public(self) -> dict:
        return {"id": self.id, "status": self.status, "stage": self.stage, "stages": self.stages, "engine": self.engine,
                "provider": self.provider, "step_index": self.step_index, "step_total": self.step_total,
                "error": self.error, "prompt": self.prompt, "started": self.started, "log": self.log[-200:],
                "resumable": self.status in ("error", "stopped") and (bool(self.pending) or self.engine == "llm")}


JOBS: dict[str, Job] = {}


def _emit(job: Job, ev: dict) -> None:
    ev["job_id"] = job.id
    ev.setdefault("ts", time.time())
    if ev["type"] in ("log", "step_start", "step_done", "step_error", "stage", "assistant"):
        job.log.append({k: v for k, v in ev.items() if k != "snapshot"})
    hub.publish(job.project_id, ev)


def log(job: Job, msg: str, level: str = "info") -> None:
    _emit(job, {"type": "log", "level": level, "msg": msg})


def set_stage(job: Job, name: str) -> None:
    if name and name != job.stage:
        job.stage = name
        if name not in job.stages:
            job.stages.append(name)
        _emit(job, {"type": "stage", "name": name, "stages": job.stages})


def save_message(pid: str, role: str, content: str, meta: dict | None = None) -> dict:
    mid = db.new_id()
    with db.tx() as c:
        c.execute("INSERT INTO messages VALUES(?,?,?,?,?,?)", (mid, pid, role, content, json.dumps(meta or {}), db.now()))
    msg = {"id": mid, "role": role, "content": content, "meta": meta or {}, "created": db.now()}
    hub.publish(pid, {"type": "message", "message": msg})
    return msg


async def _exec_step(job: Job, tool: str, args: dict, label: str, source: str) -> dict:
    """Run one real operation in a worker thread, then push the new model to the viewport."""
    rt = get_runtime(job.project_id)
    _emit(job, {"type": "step_start", "tool": tool, "label": label, "args": args, "stage": job.stage})
    t0 = time.time()
    async with rt.lock:
        if tool == "__params":
            res = await asyncio.to_thread(rt.set_params, args.get("updates") or {}, args.get("remove"))
            out = {"params": res}
            op = None
        elif tool == "__update_op":
            op = await asyncio.to_thread(rt.update_op, args["op_id"], args.get("args") or {})
            out = {"updated": op["id"]}
        elif tool == "__delete_op":
            op = await asyncio.to_thread(rt.delete_op, args["op_id"])
            out = {"deleted": op["id"]}
        elif tool == "__undo":
            op = await asyncio.to_thread(rt.undo)
            out = {"undone": op["label"] if op else None}
        else:
            r = await asyncio.to_thread(rt.apply, tool, args, label, source)
            if r.get("query"):
                _emit(job, {"type": "step_done", "tool": tool, "label": label, "result": r["result"], "query": True,
                            "ms": int((time.time() - t0) * 1000)})
                return r["result"]
            out, op = r["result"], r["op"]
        snap = await asyncio.to_thread(rt.snapshot)
    _emit(job, {"type": "step_done", "tool": tool, "label": label, "result": out, "op_id": op["id"] if op else None,
                "ms": int((time.time() - t0) * 1000), "snapshot": snap})
    if config.LIVE_BUILD_STEP_DELAY:
        await asyncio.sleep(config.LIVE_BUILD_STEP_DELAY)
    return out


# ------------------------------------------------------------------------------------------ planner
async def run_planner(job: Job, resume: bool = False) -> None:
    rt = get_runtime(job.project_id)
    if not resume:
        set_stage(job, "Analiza polecenia")
        snap = await asyncio.to_thread(rt.snapshot, False)
        p = local_plan(job.prompt, rt.mode, snap, job.selected, job.attachments)
        log(job, f"Planer offline (bez LLM): rozpoznano „{p.title}” ({p.kind}).")
        for n in p.notes:
            log(job, n, "warn")
        if p.kind == "unknown":
            job.status = "done"
            save_message(job.project_id, "assistant", "\n".join(p.notes), {"job_id": job.id, "engine": "planner"})
            return
        set_stage(job, "Plan modelowania")
        stages = list(dict.fromkeys(s["stage"] for s in p.steps))
        log(job, "Plan: " + " → ".join(stages))
        steps = list(p.steps)
        if p.params and p.kind == "create":
            existing = rt.state.get("params", {})
            steps.insert(0, {"stage": "Plan modelowania", "tool": "__params", "label": "Parametry projektu: " + ", ".join(
                f"{k}={v}" for k, v in p.params.items() if k not in existing), "args": {"updates": p.params}})
        job.pending = steps
        job.step_total = len(steps)
        job._summary = p  # type: ignore[attr-defined]
    while job.pending:
        if job.stop:
            job.status = "stopped"
            log(job, "Zatrzymano na żądanie użytkownika.", "warn")
            return
        st = job.pending[0]
        set_stage(job, st["stage"])
        job.step_index = job.step_total - len(job.pending) + 1
        await _exec_step(job, st["tool"], st["args"], st["label"], "planner")
        job.pending.pop(0)
    job.status = "done"
    p = getattr(job, "_summary", None)
    title = p.title if p else "model"
    snap = await asyncio.to_thread(rt.snapshot, False)
    notes = ("\n" + "\n".join(p.notes)) if p and p.notes else ""
    save_message(job.project_id, "assistant",
                 f"Gotowe: {title}. Wykonano {job.step_total} operacji; obiektów w scenie: {len(snap['objects'])}."
                 f"{notes}\nMożesz dalej edytować model poleceniami (np. „zwiększ wysokość o 5 mm”, „zmień kolor na …”).",
                 {"job_id": job.id, "engine": "planner"})


# ------------------------------------------------------------------------------------------ LLM agent
META_TOOLS = [
    ToolDef("report_stage", "Announce the current modeling stage (shown in the LIVE BUILD panel).",
            {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}),
    ToolDef("set_parameters", "Create/update project user parameters (numbers in model units, or colour strings). "
                              "Operations referencing '=name' are regenerated.",
            {"type": "object", "properties": {"updates": {"type": "object"}}, "required": ["updates"]}),
    ToolDef("update_operation", "Change arguments of a past operation (parametric edit); history is replayed.",
            {"type": "object", "properties": {"op_id": {"type": "string"}, "args": {"type": "object"}}, "required": ["op_id", "args"]}),
    ToolDef("delete_operation", "Remove an operation from history (history is replayed).",
            {"type": "object", "properties": {"op_id": {"type": "string"}}, "required": ["op_id"]}),
    ToolDef("undo", "Undo the last operation.", {"type": "object", "properties": {}}),
    ToolDef("get_scene", "Return objects, parameters and the operation history (ids + args).",
            {"type": "object", "properties": {}}),
]


def _engine_tooldefs(rt) -> list[ToolDef]:
    return [ToolDef(t.name, t.description, t.schema()) for t in rt.engine.tools.values()]


def _image_blocks(atts: list[dict]) -> list[dict]:
    out = []
    for a in atts:
        if not a["mime"].startswith("image/") or a["mime"] == "image/svg+xml":
            continue
        try:
            from PIL import Image
            import io

            img = Image.open(a["path"]).convert("RGB")
            img.thumbnail((1024, 1024))
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=85)
            out.append({"type": "image", "media_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()})
        except Exception:  # noqa: BLE001
            continue
    return out


async def run_llm(job: Job, resume: bool = False) -> None:
    rt = get_runtime(job.project_id)
    chosen = service.resolve("modeling", (job.provider or {}).get("provider_id"), (job.provider or {}).get("model"))
    if not chosen:
        raise ProviderError("No AI provider configured")
    provider, model, prow = chosen
    job.provider = {"provider_id": prow["id"], "name": prow["name"], "kind": prow["kind"], "model": model}
    mcp_tools = mcp_client.enabled_tools()
    tools = _engine_tooldefs(rt) + META_TOOLS + [
        ToolDef(t["alias"], f"[MCP {t['server']['name']}] {t['tool']['description']}"[:1000], t["tool"]["input_schema"])
        for t in mcp_tools]
    mcp_by_alias = {t["alias"]: t for t in mcp_tools}

    if not resume:
        set_stage(job, "Analiza polecenia")
        log(job, f"Model: {prow['name']} / {model}. Narzędzia: {len(tools)} ({len(mcp_tools)} z MCP).")
        content: list[dict] = []
        att_text = "\n".join(f"- attachment_id={a['id']} {a['filename']} ({a['mime']}, {a.get('width')}×{a.get('height')}"
                             f"{', palette ' + ','.join(a.get('palette', [])[:4]) if a.get('palette') else ''})"
                             for a in job.attachments)
        images = _image_blocks(job.attachments)
        vision = service.resolve("vision")
        if images and vision and (vision[2]["id"], vision[1]) != (prow["id"], model):
            set_stage(job, "Analiza załączników")
            log(job, f"Analiza obrazów modelem {vision[2]['name']} / {vision[1]}")
            vt = await vision[0].chat(vision[1], "Describe these reference images for a 3D artist: shapes, proportions, "
                                      "colours (hex), materials, distinctive details. Be concise.",
                                      [{"role": "user", "content": images + [{"type": "text", "text": job.prompt}]}], [], 1200)
            content.append({"type": "text", "text": f"Reference image analysis:\n{vt.text}"})
            images = []
        hist = db.q("SELECT role, content FROM messages WHERE project_id=? ORDER BY created DESC LIMIT 12", (job.project_id,))
        for h in reversed(hist[1:]):  # skip the message just saved for this job
            role = "user" if h["role"] == "user" else "assistant"
            job.convo.append({"role": role, "content": [{"type": "text", "text": h["content"][:2000]}]} if role == "user"
                             else {"role": "assistant", "text": h["content"][:2000], "tool_calls": []})
        # providers require alternating roles: merge consecutive same-role messages
        merged: list[dict] = []
        for m in job.convo:
            if merged and merged[-1]["role"] == m["role"]:
                if m["role"] == "user":
                    merged[-1]["content"] += m["content"]
                else:
                    merged[-1]["text"] += "\n" + m["text"]
            else:
                merged.append(m)
        if merged and merged[0]["role"] == "assistant":
            merged.insert(0, {"role": "user", "content": [{"type": "text", "text": "(earlier conversation)"}]})
        job.convo = merged
        user_text = job.prompt + (f"\n\nAttachments:\n{att_text}" if att_text else "")
        user_msg = {"role": "user", "content": images + content + [{"type": "text", "text": user_text}]}
        if job.convo and job.convo[-1]["role"] == "user":
            job.convo[-1]["content"] += user_msg["content"]
        else:
            job.convo.append(user_msg)
    else:
        job.convo.append({"role": "user", "content": [{"type": "text", "text":
                          f"The previous run stopped ({job.error or 'stopped by user'}). Continue the task from the current state."}]})

    for turn_no in range(60):
        if job.stop:
            job.status = "stopped"
            log(job, "Zatrzymano na żądanie użytkownika.", "warn")
            return
        snap = await asyncio.to_thread(rt.snapshot, False)
        sysmsg = system_prompt(rt.mode, rt.engine.summary(), snap["params"], snap["ops"], job.selected)
        job.step_index = turn_no + 1
        turn = await provider.chat(model, sysmsg, job.convo, tools, 8192)
        job.convo.append({"role": "assistant", "text": turn.text, "tool_calls": turn.tool_calls})
        if turn.text:
            _emit(job, {"type": "assistant", "text": turn.text})
        if not turn.tool_calls:
            job.status = "done"
            save_message(job.project_id, "assistant", turn.text or "Gotowe.", {"job_id": job.id, "engine": "llm",
                                                                               "model": model, "provider": prow["name"]})
            return
        results = []
        for tc in turn.tool_calls:
            if job.stop:
                results.append({"id": tc["id"], "name": tc["name"], "content": "Stopped by user", "is_error": True})
                continue
            name, args = tc["name"], tc.get("args") or {}
            try:
                if name == "report_stage":
                    set_stage(job, str(args.get("name", ""))[:80])
                    content = "ok"
                elif name == "set_parameters":
                    content = json.dumps(await _exec_step(job, "__params", {"updates": args.get("updates") or {}},
                                                          "Parametry: " + ", ".join(map(str, (args.get("updates") or {}).keys())), "ai"))
                elif name == "update_operation":
                    content = json.dumps(await _exec_step(job, "__update_op", args, f"Edycja operacji {args.get('op_id')}", "ai"))
                elif name == "delete_operation":
                    content = json.dumps(await _exec_step(job, "__delete_op", args, f"Usunięcie operacji {args.get('op_id')}", "ai"))
                elif name == "undo":
                    content = json.dumps(await _exec_step(job, "__undo", {}, "Cofnij", "ai"))
                elif name == "get_scene":
                    s = await asyncio.to_thread(rt.snapshot, False)
                    content = json.dumps({"objects": s["objects"], "params": s["params"], "ops": s["ops"][-80:]})[:30000]
                elif name in mcp_by_alias:
                    t = mcp_by_alias[name]
                    srv = t["server"]
                    if srv["permission"] == "ask" or t["tool"].get("destructive"):
                        ok = await hub.confirm(job.project_id, f"Narzędzie MCP: {srv['name']} → {t['tool']['name']}",
                                               json.dumps(args, ensure_ascii=False)[:1500])
                        if not ok:
                            raise EngineError("User declined this MCP tool call.")
                    _emit(job, {"type": "step_start", "tool": name, "label": f"MCP {srv['name']}: {t['tool']['name']}", "args": args})
                    content, is_err = await mcp_client.call_tool(srv, t["tool"]["name"], args)
                    _emit(job, {"type": "step_done", "tool": name, "label": f"MCP {t['tool']['name']}", "result": content[:500], "query": True})
                    if is_err:
                        raise EngineError(content)
                else:
                    label = args.pop("_label", None) if isinstance(args, dict) else None
                    out = await _exec_step(job, name, args, label or name.replace("_", " "), "ai")
                    content = json.dumps(out, default=str)[:8000]
                results.append({"id": tc["id"], "name": name, "content": content, "is_error": False})
            except (EngineError, KeyError, ValueError) as e:
                _emit(job, {"type": "step_error", "tool": name, "error": str(e)})
                results.append({"id": tc["id"], "name": name, "content": f"ERROR: {e}", "is_error": True})
        job.convo.append({"role": "tool", "results": results})
    job.status = "done"
    save_message(job.project_id, "assistant", "Osiągnięto limit 60 tur agenta — przerwano.", {"job_id": job.id})


# ------------------------------------------------------------------------------------------ lifecycle
async def _runner(job: Job, resume: bool) -> None:
    job.status = "running"
    job.stop = False
    job.error = ""
    _emit(job, {"type": "job", "job": job.public()})
    try:
        if job.engine == "llm":
            await run_llm(job, resume)
        else:
            await run_planner(job, resume)
    except asyncio.CancelledError:
        job.status = "stopped"
    except (EngineError, ProviderError) as e:
        job.status, job.error = "error", str(e)
        _emit(job, {"type": "step_error", "error": str(e)})
        log(job, f"Błąd: {e}. Możesz poprawić polecenie lub kliknąć „Wznów”.", "error")
    except Exception as e:  # noqa: BLE001
        job.status, job.error = "error", f"{type(e).__name__}: {e}"
        log(job, f"Nieoczekiwany błąd: {job.error}", "error")
        traceback.print_exc()
    finally:
        if job.status == "done":
            try:
                from .routes.projects import save_version

                save_version(job.project_id, "Auto: " + job.prompt[:60])
            except Exception:  # noqa: BLE001
                pass
        _emit(job, {"type": "job", "job": job.public()})


def start(pid: str, prompt: str, attachments: list[dict], selected: str | None, provider: dict | None) -> Job:
    cur = JOBS.get(pid)
    if cur and cur.status == "running":
        raise EngineError("A build is already running for this project — stop it first.")
    try:
        if provider and provider.get("provider_id") == "offline":
            use_llm = False
        elif provider:
            use_llm = bool(service.resolve("modeling", provider.get("provider_id"), provider.get("model")))
        else:
            use_llm = bool(service.resolve("modeling"))
    except ProviderError as e:
        raise EngineError(str(e)) from e
    job = Job(uuid.uuid4().hex[:10], pid, prompt, attachments, selected, "llm" if use_llm else "planner",
              provider if use_llm else None)
    JOBS[pid] = job
    job.task = asyncio.create_task(_runner(job, False))
    return job


def stop(pid: str) -> Job | None:
    job = JOBS.get(pid)
    if job and job.status == "running":
        job.stop = True
        if job.engine == "llm" and job.task:
            # LLM calls can take long: cancel the in-flight HTTP request too
            asyncio.get_running_loop().call_later(1.5, lambda: job.task and not job.task.done() and job.task.cancel())
    return job


def resume(pid: str) -> Job:
    job = JOBS.get(pid)
    if not job or job.status not in ("error", "stopped"):
        raise EngineError("Nothing to resume.")
    if job.engine == "planner" and not job.pending:
        raise EngineError("Nothing left to resume.")
    if job.engine == "planner" and job.status == "error":
        log(job, f"Pomijam nieudany krok: {job.pending[0]['label']}", "warn")
        job.pending.pop(0)
    job.task = asyncio.create_task(_runner(job, True))
    return job
