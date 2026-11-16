"""AI TEAM orchestrator. All inter-agent communication goes through this backend; agents never talk to
each other directly and never write geometry directly — every change is an operation in the central
history (validated, authored, undoable).

Pipeline:  PLAN (lead) → independent CRITIQUES → DEBATE → DECISION (lead) → BUILD (engineer, then quality)
           → [QA (code) → independent REVIEWS → FIX cross-check → lead approval → apply → re-QA] × rounds
           → SUMMARY."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from .. import db
from ..ai.providers.base import ToolDef
from ..engines.base import EngineError
from ..hub import hub
from ..jobs import META_TOOLS, Job, _emit, _exec_step, _image_blocks, log, save_message, set_stage
from ..qa import run_qa
from ..runtime import get_runtime
from . import config as tcfg
from . import context
from .llm import AgentDown, Budget, LimitExceeded, call, call_json

# role → tools it may use when acting on the engine
CAD_FINISH = {"set_color", "apply_image", "add_text", "fillet", "chamfer"}
GAME_LOOK = {"set_material", "uv_unwrap", "apply_decal", "set_texture_resolution", "smooth"}
QUERY_TOOLS = {"measure", "measure_distance", "list_faces", "scene_stats", "list_material_presets"}


def role_tools(mode: str, roles: list[str], engine_tools: dict) -> set[str]:
    allowed: set[str] = set(QUERY_TOOLS)
    finish = CAD_FINISH if mode == "cad" else GAME_LOOK
    if "engineer" in roles:
        allowed |= {n for n in engine_tools if n not in finish or mode == "cad"}  # CAD engineer may fillet/chamfer too
    if "quality" in roles:
        allowed |= finish
    if "lead" in roles and len(roles) == 3:
        allowed |= set(engine_tools)
    return {t for t in allowed if t in engine_tools}


def sys_prompt(member: dict, roles: list[str], mode: str) -> str:
    labels = ", ".join(tcfg.ROLES[r]["label"] for r in roles)
    duties = "; ".join(tcfg.ROLES[r]["duty"] for r in roles)
    from ..ai.prompts import CAD, GAME

    return (f"You are agent '{member['label'] or member.get('model_resolved') or member['model']}' in an AI 3D modeling studio team "
            f"made of several independent AI models. Your role(s): {labels}. Your duties: {duties}.\n"
            "Rules: think independently and critically — do NOT simply agree with other agents; if you agree, say exactly why; "
            "if you see a problem, name the object, the evidence and a concrete fix. Never invent measurements: measured facts "
            "exist only in the TECHNICAL QA section; anything else is your opinion and must be marked as unverified. "
            "Human-readable text in the user's language (Polish if the brief is Polish).\n\n"
            + (CAD if mode == "cad" else GAME).split("Workflow:")[0] + (CAD if mode == "cad" else GAME).split("\n\nMODE:")[1])


class TeamRun:
    def __init__(self, job: Job, cfg: dict):
        self.job = job
        self.cfg = cfg
        self.rt = get_runtime(job.project_id)
        self.pid = job.project_id
        self.members = [m for m in cfg["members"] if m["enabled"]]
        if job.team.get("only_member"):
            self.members = [m for m in self.members if m["id"] == job.team["only_member"]]
        self.failed: set[str] = set(job.team.get("failed", []))
        self.budget = Budget(cfg)
        self.states: dict[str, dict] = job.team.setdefault("states", {})
        for m in self.members:
            self.states.setdefault(m["id"], {"status": "idle", "task": "", "last_message": "", "issues": 0, "ops": 0,
                                             "tokens_in": 0, "tokens_out": 0, "calls": 0, "errors": 0})
        self.t = job.team  # shared memory (serialisable)
        self.t.setdefault("issues", [])
        self.t.setdefault("round", 0)
        self.t.setdefault("done", [])

    # ------------------------------------------------------------------ infra
    @property
    def roles(self) -> dict[str, list[str]]:
        return tcfg.assign_roles(self.members, self.failed)

    def who(self, role: str) -> dict | None:
        return tcfg.member_for(role, self.roles, self.members)

    def name(self, m: dict) -> str:
        return m["label"] or m.get("model_resolved") or m["model"] or m["provider_id"]

    def publish_state(self) -> None:
        roles = self.roles
        self.t["members"] = [{"id": m["id"], "name": self.name(m), "provider_id": m["provider_id"], "model": m.get("model_resolved") or m["model"],
                              "color": m["color"], "roles": roles.get(m["id"], []), "failed": m["id"] in self.failed,
                              **self.states[m["id"]]} for m in self.members]
        self.t["budget"] = {"tokens": self.budget.tokens, "seconds": int(time.time() - self.budget.t0), "cost": round(self.budget.cost, 4)}
        self.t["failed"] = sorted(self.failed)
        hub.publish(self.pid, {"type": "team_state", "team": self.public()})

    def public(self) -> dict:
        from ..jobs import team_public

        return team_public(self.t)

    def status(self, m: dict | None, st: str, task: str = "") -> None:
        if not m:
            return
        self.states[m["id"]]["status"] = st
        self.states[m["id"]]["task"] = task
        self.publish_state()

    def post(self, m: dict | None, phase: str, kind: str, content: str, data: Any = None, tokens: int = 0) -> None:
        mid = db.new_id()
        role = ",".join(self.roles.get(m["id"], [])) if m else "system"
        row = (mid, self.pid, self.job.id, self.t["round"], phase, m["id"] if m else "system", self.name(m) if m else "System",
               role, kind, content, json.dumps(data, ensure_ascii=False, default=str) if data is not None else None, tokens, db.now())
        with db.tx() as c:
            c.execute("INSERT INTO team_messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
        if m:
            self.states[m["id"]]["last_message"] = content[:240]
        msg = {"id": mid, "job_id": self.job.id, "round": self.t["round"], "phase": phase, "agent_id": row[5], "agent_name": row[6],
               "role": role, "kind": kind, "content": content, "data": data, "tokens": tokens, "created": row[12],
               "color": m["color"] if m else "#646c76"}
        hub.publish(self.pid, {"type": "team_message", "message": msg})
        self.publish_state()

    def check_stop(self) -> None:
        if self.job.stop:
            raise asyncio.CancelledError()
        self.budget.check()

    def mark_failed(self, m: dict, err: str) -> None:
        self.failed.add(m["id"])
        self.states[m["id"]]["status"] = "error"
        self.post(None, "system", "error", f"Agent {self.name(m)} nie odpowiada ({err[:160]}). "
                  "Jego rola przechodzi na pozostałych agentów.", {"agent": self.name(m), "error": err})
        if not [x for x in self.members if x["id"] not in self.failed]:
            raise AgentDown("All agents are unavailable")

    async def ask_json(self, m: dict, purpose: str, instruction: str, status: str, task: str, phase: str,
                       kind: str, images: bool = False, extra: dict | None = None) -> dict | None:
        """One JSON call; on hard failure the agent is marked down and None is returned."""
        self.check_stop()
        self.status(m, status, task)
        ctx = context.build(self.rt, self.t, purpose, extra)
        imgs = _image_blocks(self.t.get("attachments_full", [])) if images else []
        st = self.states[m["id"]]
        before = st.get("tokens_in", 0) + st.get("tokens_out", 0)
        try:
            data, turn = await call_json(m, sys_prompt(m, self.roles.get(m["id"], ["reviewer"]), self.rt.mode),
                                         f"{ctx}\n\n## YOUR TASK\n{instruction}", self.budget, self.cfg["retries"], st, imgs)
        except AgentDown as e:
            self.mark_failed(m, str(e))
            return None
        used = st.get("tokens_in", 0) + st.get("tokens_out", 0) - before
        text = data.get("summary") or data.get("concept") or data.get("position") or data.get("verdict") or ""
        self.post(m, phase, kind, str(text)[:2000], data, used)
        self.status(m, "idle")
        return data

    # ------------------------------------------------------------------ phases
    async def phase_plan(self) -> None:
        set_stage(self.job, "Plan (Lead Designer)")
        lead = self.who("lead")
        while lead:
            data = await self.ask_json(lead, "plan", PLAN_TASK, "thinking", "Analiza polecenia i plan", "plan", "plan", images=True)
            if data is not None:
                self.t["plan"] = data
                return
            lead = self.who("lead")
        raise AgentDown("No lead designer available")

    async def phase_critique(self) -> None:
        set_stage(self.job, "Niezależna krytyka planu")
        lead = self.who("lead")
        critics = [m for m in self.members if m["id"] not in self.failed and (m is not lead or len(self.active()) == 1)]
        task = CRITIQUE_TASK + (SELF_CHECK if len(self.active()) == 1 else "")
        res = await asyncio.gather(*[self.ask_json(m, "critique", task, "analyzing", "Krytyka planu", "critique", "critique",
                                                   images=True) for m in critics])
        self.t["critiques"] = {self.name(m): r for m, r in zip(critics, res) if r is not None}

    def active(self) -> list[dict]:
        return [m for m in self.members if m["id"] not in self.failed]

    async def phase_debate(self) -> None:
        crit = self.t.get("critiques") or {}
        if not self.cfg["debate"] or len(crit) < 2 and not (len(crit) == 1 and len(self.active()) >= 2):
            return
        set_stage(self.job, "Debata")
        self.t["debate"] = []
        participants = self.active()
        for r in range(self.cfg["max_debate_rounds"]):
            res = await asyncio.gather(*[self.ask_json(m, "debate", DEBATE_TASK, "analyzing", f"Debata — runda {r + 1}", "debate", "debate")
                                         for m in participants])
            entries = [{"agent": self.name(m), **d} for m, d in zip(participants, res) if d]
            self.t["debate"].extend(entries)
            disagreements = [s for e in entries for s in e.get("stances", []) if s.get("stance") in ("disagree", "partial")]
            self.t.setdefault("conflicts", []).extend(
                {"round": r + 1, "agent": e["agent"], **s} for e in entries for s in e.get("stances", []) if s.get("stance") == "disagree")
            if not disagreements:
                self.post(None, "debate", "info", "Brak sporów — agenty zgadzają się co do planu (z uzasadnieniem).")
                break

    async def phase_decide(self) -> None:
        set_stage(self.job, "Decyzja Lead Designera")
        lead = self.who("lead")
        if not self.t.get("critiques"):
            self.t["final_plan"] = self.t["plan"]
            return
        while lead:
            data = await self.ask_json(lead, "decide", DECIDE_TASK, "thinking", "Wybór rozwiązań", "decision", "decision")
            if data is not None:
                self.t["final_plan"] = data.get("final_plan") or self.t["plan"]
                self.t["decisions"] = data.get("decisions", [])
                return
            lead = self.who("lead")
        self.t["final_plan"] = self.t["plan"]

    async def tool_loop(self, m: dict, roles: list[str], instruction: str, purpose: str, status: str, task: str,
                        max_turns: int) -> int:
        """Agent drives the engine through tool calls; every call goes through the central operation system."""
        tools_allowed = role_tools(self.rt.mode, roles, self.rt.engine.tools)
        tools = [ToolDef(t.name, t.description, t.schema()) for n, t in self.rt.engine.tools.items() if n in tools_allowed]
        tools += [t for t in META_TOOLS if t.name in ("report_stage", "set_parameters", "get_scene")]
        convo: list[dict] = [{"role": "user", "content": [{"type": "text", "text":
                              context.build(self.rt, self.t, purpose) + f"\n\n## YOUR TASK\n{instruction}\n"
                              f"You may only use these tools: {', '.join(sorted(tools_allowed))}. Finish with a short text summary."}]}]
        n_ops = 0
        self.status(m, status, task)
        for _ in range(max_turns):
            self.check_stop()
            try:
                turn = await call(m, sys_prompt(m, roles, self.rt.mode), convo, tools, self.budget, self.cfg["retries"],
                                  self.states[m["id"]], 8192)
            except AgentDown as e:
                self.mark_failed(m, str(e))
                return -1
            convo.append({"role": "assistant", "text": turn.text, "tool_calls": turn.tool_calls})
            if turn.text:
                self.states[m["id"]]["last_message"] = turn.text[:240]
                _emit(self.job, {"type": "assistant", "text": f"[{self.name(m)}] {turn.text}"})
            if not turn.tool_calls:
                self.post(m, purpose, "build", turn.text or "Zakończono.", {"ops": n_ops})
                break
            results = []
            for tc in turn.tool_calls:
                name, args = tc["name"], tc.get("args") or {}
                try:
                    if name == "report_stage":
                        set_stage(self.job, f"{args.get('name', '')} ({self.name(m)})"[:90])
                        content = "ok"
                    elif name == "get_scene":
                        s = self.rt.snapshot(False)
                        content = json.dumps({"objects": s["objects"], "params": s["params"], "ops": s["ops"][-60:]}, default=str)[:30000]
                    elif name == "set_parameters":
                        content = json.dumps(await _exec_step(self.job, "__params", {"updates": args.get("updates") or {}},
                                                              "Parametry", "ai", self.name(m)), default=str)
                    elif name not in tools_allowed:
                        raise EngineError(f"Tool '{name}' is not allowed for your role(s) {roles}.")
                    else:
                        out = await _exec_step(self.job, name, args, args.pop("_label", None) or name.replace("_", " "), "ai", self.name(m))
                        content = json.dumps(out, default=str)[:8000]
                        if not self.rt.engine.tools[name].mutating:
                            pass
                        else:
                            n_ops += 1
                            self.states[m["id"]]["ops"] += 1
                    results.append({"id": tc["id"], "name": name, "content": content, "is_error": False})
                except (EngineError, KeyError, ValueError) as e:
                    _emit(self.job, {"type": "step_error", "tool": name, "error": str(e), "author": self.name(m)})
                    results.append({"id": tc["id"], "name": name, "content": f"ERROR: {e}", "is_error": True})
            convo.append({"role": "tool", "results": results})
        self.status(m, "idle")
        return n_ops

    async def phase_build(self) -> None:
        eng = self.who("engineer")
        qual = self.who("quality")
        if eng and qual and eng["id"] == qual["id"]:
            set_stage(self.job, "Budowa modelu")
            r = await self.tool_loop(eng, self.roles[eng["id"]], BUILD_ALL_TASK, "build", "modeling", "Geometria i materiały",
                                     self.cfg["build_max_turns"])
            if r < 0:
                return await self.phase_build()
            return
        if eng:
            set_stage(self.job, "Geometria (3D Engineer)")
            r = await self.tool_loop(eng, self.roles[eng["id"]], BUILD_GEOMETRY_TASK, "build", "modeling", "Budowa geometrii",
                                     self.cfg["build_max_turns"])
            if r < 0:
                return await self.phase_build()
        qual = self.who("quality")
        if qual:
            set_stage(self.job, "Materiały i detale (Quality Specialist)")
            r = await self.tool_loop(qual, self.roles[qual["id"]], BUILD_LOOK_TASK, "build", "modeling", "Materiały, kolory, tekstury",
                                     max(6, self.cfg["build_max_turns"] // 2))
            if r < 0:
                q2 = self.who("quality")
                if q2:
                    await self.tool_loop(q2, self.roles[q2["id"]], BUILD_LOOK_TASK, "build", "modeling", "Materiały (zastępstwo)",
                                         max(6, self.cfg["build_max_turns"] // 2))

    async def run_qa(self, stage: str) -> dict:
        set_stage(self.job, f"Kontrola techniczna ({stage})")
        _emit(self.job, {"type": "step_start", "tool": "qa", "label": "Testy techniczne (geometria, siatka, eksport)"})
        rep = await asyncio.to_thread(run_qa, self.rt)
        self.t["qa"] = rep
        with db.tx() as c:
            c.execute("INSERT INTO team_qa VALUES(?,?,?,?,?,?,?)", (db.new_id(), self.pid, self.job.id, self.t["round"], stage,
                                                                   json.dumps(rep, default=str), db.now()))
        _emit(self.job, {"type": "step_done", "tool": "qa", "label": f"Testy techniczne: wynik {rep['score']}", "query": True,
                         "result": {"failed": rep["failed"], "warnings": rep["warnings"], "unchecked": rep["unchecked"]}})
        hub.publish(self.pid, {"type": "team_qa", "report": rep, "stage": stage, "round": self.t["round"]})
        return rep

    def _issue_row(self, i: dict) -> None:
        with db.tx() as c:
            c.execute("INSERT OR REPLACE INTO team_issues VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                i["id"], self.pid, self.job.id, i["round"], i["author_id"], i["author"], i["severity"], i["category"], i["target"],
                i["problem"], i.get("evidence", ""), i.get("qa_check"), int(i["backed_by_test"]), json.dumps(i.get("fix")), i["status"],
                json.dumps(i.get("reviews", [])), json.dumps({**(i.get("decision") or {}), "undo": i.get("undo")} if i.get("decision") or i.get("undo") else None),
                json.dumps(i.get("op_ids", [])), i.get("version_before"),
                i["created"], db.now()))
        hub.publish(self.pid, {"type": "team_issue", "issue": i})

    def validate_fix(self, fix: dict | None) -> str | None:
        """Engine-side validation before an operation may enter the queue."""
        if not fix or not isinstance(fix, dict) or not fix.get("tool"):
            return "no executable fix"
        args = fix.get("args") or {}
        if fix["tool"] == "set_parameters":
            up = args.get("updates")
            if not isinstance(up, dict) or not up:
                return "set_parameters needs non-empty 'updates'"
            unknown = [k for k in up if k not in self.rt.state.get("params", {})]
            return f"unknown parameters {unknown}" if unknown else None
        if fix["tool"] == "update_operation":
            if not any(o["id"] == args.get("op_id") for o in self.rt.state["ops"]):
                return f"operation {args.get('op_id')} not found"
            return None if isinstance(args.get("args"), dict) else "update_operation needs 'args'"
        spec = self.rt.engine.tools.get(fix["tool"])
        if not spec:
            return f"unknown tool {fix['tool']}"
        if not spec.mutating:
            return "query tool cannot fix anything"
        args = fix.get("args") or {}
        missing = [r for r in spec.required if r not in args]
        if missing:
            return f"missing args {missing}"
        names = {o["name"] for o in self.rt.engine.objects()}
        for k in ("target", "tool", "source"):
            if k in args and isinstance(args[k], str) and k != "tool" and args[k] not in names:
                return f"object '{args[k]}' does not exist"
        if isinstance(args.get("target"), str) and args["target"] in set(self.rt.state.get("locked", [])):
            return f"object '{args['target']}' is locked"
        return None

    async def phase_reviews(self) -> list[dict]:
        set_stage(self.job, f"Przegląd agentów (runda {self.t['round']})")
        reviewers = self.active()
        tools_doc = "\n".join(f"- {t.name}: required {t.required}" for t in self.rt.engine.tools.values() if t.mutating)
        tools_doc += ("\n- set_parameters: args {\"updates\": {param: value}} (preferred for parametric models)"
                      "\n- update_operation: args {\"op_id\": id, \"args\": {...}} (edit a past operation)")
        task = REVIEW_TASK.format(tools=tools_doc) + (SELF_CHECK if len(reviewers) == 1 else "")
        res = await asyncio.gather(*[self.ask_json(m, "review", task, "reviewing", "Kontrola jakości modelu", "review", "review",
                                                   images=True) for m in reviewers])
        qa_ids = {c["id"]: c for c in self.t["qa"]["checks"]}
        new: list[dict] = []
        ai_scores = self.t.setdefault("ai_scores", {})
        seen: dict[str, dict] = {}  # identical fixes from several agents become one issue "confirmed by" others
        for m, r in zip(reviewers, res):
            if not r:
                continue
            ai_scores[self.name(m)] = {"round": self.t["round"], "scores": r.get("ai_scores", {}), "verdict": r.get("verdict")}
            for k, it in enumerate(r.get("issues", [])[:12]):
                qc = it.get("qa_check")
                backed = bool(qc and qc in qa_ids and qa_ids[qc]["status"] in ("fail", "warn"))
                sev = it.get("severity", "minor") if it.get("severity") in tcfg.SEVERITY_ORDER else "minor"
                issue = {"id": db.new_id(), "round": self.t["round"], "author_id": m["id"], "author": self.name(m), "severity": sev,
                         "category": str(it.get("category", ""))[:40], "target": str(it.get("target", ""))[:80],
                         "problem": str(it.get("problem", ""))[:600], "evidence": str(it.get("evidence", ""))[:400],
                         "qa_check": qc if qc in qa_ids else None, "backed_by_test": backed, "fix": it.get("fix"),
                         "status": "proposed", "reviews": [], "decision": None, "op_ids": [], "created": db.now()}
                key = json.dumps(issue["fix"], sort_keys=True, default=str) if issue["fix"] else None
                if key and key in seen:
                    seen[key].setdefault("confirmed_by", []).append(self.name(m))
                    seen[key]["reviews"].append({"by": self.name(m), "approve": True, "reason": "Niezależnie zgłosił ten sam problem"})
                    self._issue_row(seen[key])
                    self.states[m["id"]]["issues"] += 1
                    continue
                if key:
                    seen[key] = issue
                err = self.validate_fix(issue["fix"])
                if err:
                    issue["status"] = "invalid"
                    issue["decision"] = {"by": "system", "decision": "reject", "reason": f"Walidacja silnika: {err}"}
                new.append(issue)
                self.states[m["id"]]["issues"] += 1
                self._issue_row(issue)
        self.t["issues"].extend(new)
        self.publish_state()
        return [i for i in new if i["status"] == "proposed"]

    async def phase_cross_check(self, issues: list[dict]) -> None:
        if not issues:
            return
        set_stage(self.job, "Wzajemna weryfikacja poprawek")
        act = self.active()
        groups: dict[str, list[dict]] = {}
        for k, i in enumerate(issues):
            others = [m for m in act if m["id"] != i["author_id"]] or act  # 1 model: fresh self-check call
            v = others[k % len(others)]
            groups.setdefault(v["id"], []).append(i)
        by_id = {m["id"]: m for m in act}
        fixes_for = lambda lst: [{"issue_id": i["id"], "author": i["author"], "severity": i["severity"], "target": i["target"],
                                  "problem": i["problem"], "evidence": i["evidence"], "backed_by_test": i["backed_by_test"],
                                  "fix": i["fix"]} for i in lst]
        res = await asyncio.gather(*[self.ask_json(by_id[vid], "fix_check", FIX_CHECK_TASK, "reviewing", "Weryfikacja propozycji innych agentów",
                                                   "fix_check", "fix_check", extra={"fixes": fixes_for(lst)})
                                     for vid, lst in groups.items()])
        for (vid, lst), r in zip(groups.items(), res):
            for c in (r or {}).get("checks", []):
                for i in lst:
                    if i["id"] == c.get("issue_id"):
                        i["reviews"].append({"by": self.name(by_id[vid]), "approve": bool(c.get("approve")), "reason": str(c.get("reason", ""))[:400]})
                        i["status"] = "validated" if c.get("approve") else "disputed"
                        self._issue_row(i)

    async def phase_fix_decision(self, issues: list[dict]) -> list[dict]:
        cand = [i for i in issues if i["status"] in ("validated", "disputed", "proposed")]
        if not cand:
            return []
        set_stage(self.job, "Decyzja o poprawkach (Lead Designer)")
        lead = self.who("lead")
        data = None
        while lead and data is None:
            data = await self.ask_json(lead, "fix_decide", FIX_DECIDE_TASK, "thinking", "Zatwierdzanie poprawek", "fix_decision", "decision",
                                       extra={"fixes": [{"issue_id": i["id"], "author": i["author"], "severity": i["severity"], "target": i["target"],
                                                         "problem": i["problem"], "backed_by_test": i["backed_by_test"], "fix": i["fix"]} for i in cand],
                                              "fix_checks": [{"issue_id": i["id"], "reviews": i["reviews"]} for i in cand]})
            if data is None:
                lead = self.who("lead")
        decisions = {d.get("issue_id"): d for d in (data or {}).get("decisions", [])}
        approved = []
        for i in cand:
            d = decisions.get(i["id"])
            if not d:
                i["status"], i["decision"] = "rejected", {"by": "system", "decision": "reject", "reason": "Lead nie podjął decyzji"}
            elif d.get("decision") == "approve":
                i["decision"] = {"by": self.name(lead) if lead else "?", "decision": "approve", "reason": str(d.get("reason", ""))[:400]}
                i["status"] = "approved"
                approved.append(i)
            else:
                i["status"] = "rejected"
                i["decision"] = {"by": self.name(lead) if lead else "?", "decision": "reject", "reason": str(d.get("reason", ""))[:400]}
            self._issue_row(i)
        return approved

    async def apply_fixes(self, approved: list[dict]) -> int:
        mode = self.cfg["mode"]
        limit = tcfg.SEVERITY_ORDER[self.cfg["auto_apply_max_severity"]]
        if mode == "review" or any(tcfg.SEVERITY_ORDER[i["severity"]] > limit for i in approved):
            for i in approved:
                if mode == "review" or tcfg.SEVERITY_ORDER[i["severity"]] > limit:
                    i["status"] = "pending_user"
                    self._issue_row(i)
            pending = [i for i in approved if i["status"] == "pending_user"]
            if pending:
                set_stage(self.job, "Oczekiwanie na decyzję użytkownika")
                self.post(None, "fix", "info", f"{len(pending)} poprawek czeka na Twoje zatwierdzenie (zakładka Poprawki).")
                await self.wait_user(pending)
        todo = [i for i in approved if i["status"] in ("approved", "user_approved")]
        if not todo:
            return 0
        from ..routes.projects import save_version

        ver = save_version(self.pid, f"Przed poprawkami — runda {self.t['round']}")
        set_stage(self.job, f"Naprawa ({len(todo)} poprawek)")
        applied = 0
        for i in todo:
            self.check_stop()
            author = next((m for m in self.members if m["id"] == i["author_id"]), None)
            self.status(author, "fixing", f"Poprawka: {i['problem'][:60]}")
            fix = i["fix"]
            n0 = len(self.rt.state["ops"])
            label = f"Poprawka: {fix.get('description') or i['problem']}"[:120]
            args = dict(fix.get("args") or {})
            try:
                if fix["tool"] == "set_parameters":
                    i["undo"] = {"kind": "params", "prev": {k: self.rt.state["params"].get(k) for k in args["updates"]}}
                    await _exec_step(self.job, "__params", {"updates": args["updates"]}, label, "ai", i["author"])
                elif fix["tool"] == "update_operation":
                    prev = next(o for o in self.rt.state["ops"] if o["id"] == args["op_id"])
                    i["undo"] = {"kind": "op", "op_id": args["op_id"], "prev_args": prev["args"]}
                    await _exec_step(self.job, "__update_op", {"op_id": args["op_id"], "args": args["args"]}, label, "ai", i["author"])
                else:
                    await _exec_step(self.job, fix["tool"], args, label, "ai", i["author"])
                    i["undo"] = {"kind": "ops"}
                i["op_ids"] = [o["id"] for o in self.rt.state["ops"][n0:]]
                i["status"] = "applied"
                i["version_before"] = ver["id"]
                applied += 1
                if author:
                    self.states[author["id"]]["ops"] += 1
            except EngineError as e:
                i["status"], i["error"] = "failed", str(e)
                i["decision"] = {**(i.get("decision") or {}), "engine_error": str(e)}
            self._issue_row(i)
            self.status(author, "idle")
        return applied

    async def wait_user(self, pending: list[dict]) -> None:
        ev: asyncio.Event = asyncio.Event()
        WAITERS[self.job.id] = ev
        try:
            while any(i["status"] == "pending_user" for i in pending):
                self.check_stop()
                ev.clear()
                try:
                    await asyncio.wait_for(ev.wait(), 2.0)
                except asyncio.TimeoutError:
                    pass
                for i in pending:
                    row = db.q1("SELECT status FROM team_issues WHERE id=?", (i["id"],))
                    if row:
                        i["status"] = row["status"]
        finally:
            WAITERS.pop(self.job.id, None)

    async def phase_summary(self) -> None:
        lead = self.who("lead")
        if not lead:
            return
        set_stage(self.job, "Podsumowanie")
        self.status(lead, "thinking", "Podsumowanie")
        try:
            turn = await call(lead, sys_prompt(lead, self.roles[lead["id"]], self.rt.mode),
                              [{"role": "user", "content": [{"type": "text", "text": context.build(self.rt, self.t, "summary") + SUMMARY_TASK}]}],
                              [], self.budget, self.cfg["retries"], self.states[lead["id"]], 1500)
            text = turn.text.strip() or "Gotowe."
        except AgentDown as e:
            self.mark_failed(lead, str(e))
            text = "Zespół zakończył pracę (podsumowanie niedostępne — lead nie odpowiada)."
        self.post(lead, "summary", "summary", text)
        qa = self.t.get("qa") or {}
        verified = not qa.get("failed") and qa.get("score") is not None
        save_message(self.pid, "assistant", text + f"\n\nTesty techniczne: {qa.get('score')} / 100"
                     + (f" · niesprawdzone: {len(qa.get('unchecked', []))}" if qa.get("unchecked") else "")
                     + ("" if verified else " · model NIE jest zweryfikowany (są nieudane testy)"),
                     {"job_id": self.job.id, "engine": "team", "agents": [self.name(m) for m in self.members]})
        self.status(lead, "completed")

    # ------------------------------------------------------------------ main
    async def run(self, resume: bool = False) -> None:
        if not self.members:
            raise AgentDown("Brak aktywnych agentów w AI TEAM (Ustawienia → AI Team).")
        self.publish_state()
        done = self.t["done"]
        if self.t.get("task") and self.t["task"] != "full":
            await self.run_manual()
            return
        for phase, fn in (("plan", self.phase_plan), ("critique", self.phase_critique), ("debate", self.phase_debate),
                          ("decide", self.phase_decide), ("build", self.phase_build)):
            if phase in done:
                continue
            await fn()
            done.append(phase)
        while self.t["round"] < self.cfg["max_rounds"]:
            self.t["round"] += 1
            before = await self.run_qa("przed przeglądem")
            proposed = await self.phase_reviews()
            await self.phase_cross_check(proposed)
            approved = await self.phase_fix_decision(proposed)
            applied = await self.apply_fixes(approved)
            if not applied:
                self.post(None, "qa", "info", f"Runda {self.t['round']}: brak zatwierdzonych poprawek — kontrola zakończona.")
                break
            after = await self.run_qa("po poprawkach")
            self.t.setdefault("comparisons", []).append({"round": self.t["round"], "before": _qa_brief(before), "after": _qa_brief(after),
                                                        "applied": applied})
            hub.publish(self.pid, {"type": "team_state", "team": self.public()})
        else:
            if self.cfg["max_rounds"] > 0:
                self.post(None, "qa", "info", f"Osiągnięto limit {self.cfg['max_rounds']} rund kontroli.")
        if not self.t.get("qa") or self.t["qa"]["rev"] != self.rt.state["rev"]:
            await self.run_qa("końcowa")
        await self.phase_summary()
        for m in self.active():
            self.states[m["id"]]["status"] = "completed"
        self.publish_state()

    async def run_manual(self) -> None:
        """MANUAL mode: the user picked one agent and one task."""
        m = next((x for x in self.members if x["id"] == self.t["only_member"]), None)
        if not m:
            raise AgentDown("Wybrany agent jest niedostępny")
        task = self.t["task"]
        roles = self.roles.get(m["id"], list(tcfg.CORE_ROLES))
        if task == "plan":
            set_stage(self.job, f"Plan — {self.name(m)}")
            self.t["plan"] = await self.ask_json(m, "plan", PLAN_TASK, "thinking", "Plan", "plan", "plan", images=True)
        elif task == "build":
            set_stage(self.job, f"Modelowanie — {self.name(m)}")
            await self.tool_loop(m, list(tcfg.CORE_ROLES), BUILD_ALL_TASK, "build", "modeling", "Modelowanie", self.cfg["build_max_turns"])
        elif task == "review":
            self.t["round"] += 1
            await self.run_qa("ręczna")
            self.members = [m]
            await self.phase_reviews()
        elif task == "fix":
            set_stage(self.job, f"Poprawki — {self.name(m)}")
            await self.run_qa("przed poprawką")
            await self.tool_loop(m, roles if roles else list(tcfg.CORE_ROLES), FIX_FREE_TASK, "review", "fixing", "Poprawki",
                                 self.cfg["build_max_turns"])
            await self.run_qa("po poprawce")
        self.status(m, "completed")


WAITERS: dict[str, asyncio.Event] = {}


def _qa_brief(r: dict) -> dict:
    return {"score": r["score"], "rev": r["rev"], "failed": r["failed"], "warnings": r["warnings"],
            "categories": {k: v["score"] for k, v in r["categories"].items()},
            "checks": {c["id"]: {"status": c["status"], "value": c["value"]} for c in r["checks"]}}


# ============================================================================ task prompts
PLAN_TASK = """Analyse the brief (and reference images if any) and write the modeling plan. Reply with ONLY JSON:
{"concept": "short design concept", "dimensions": {"overall": "...", "key": "..."},
 "parameters": {"name": value, ...}, "steps": [{"stage": "...", "description": "...", "owner": "engineer|quality"}],
 "materials": ["..."], "risks": ["what could go wrong"], "summary": "2-3 sentences for the user"}"""

CRITIQUE_TASK = """Critically review the Lead Designer's plan from the perspective of YOUR role. Work independently.
Find concrete problems (proportions, dimensions, missing parts, printability, topology, materials, realism, fidelity to
the references). Approving is allowed only with a specific justification. Reply with ONLY JSON:
{"verdict": "approve|changes", "issues": [{"id": "I1", "severity": "minor|major|critical", "area": "...",
 "problem": "...", "proposal": "..."}], "improvements": ["..."], "confidence": 0-100, "position": "one-sentence stance"}"""

SELF_CHECK = """
NOTE: you are the only model in the team. Perform this as a SEPARATE self-check with fresh eyes: assume the earlier
work contains mistakes and look for them deliberately."""

DEBATE_TASK = """Read the other agents' critiques (and your own). For each item where you have an opinion, take a stance
with an argument — agree, disagree or partial. Disagree when you have reasons; do not just agree to be polite.
Reply with ONLY JSON: {"stances": [{"item": "<agent>:<issue id>", "stance": "agree|disagree|partial", "argument": "..."}],
"position": "your updated position in one sentence"}"""

DECIDE_TASK = """You are the Lead Designer: weigh the critiques and the debate, then decide. Accept good proposals, reject
weak ones with a reason, resolve conflicts explicitly (state which side wins and why). Reply with ONLY JSON:
{"decisions": [{"item": "<agent>:<issue id>", "decision": "accept|reject|modify", "reason": "..."}],
 "conflicts_resolved": [{"topic": "...", "sides": "...", "ruling": "..."}],
 "final_plan": {"concept": "...", "parameters": {...}, "steps": [{"stage": "...", "description": "...", "owner": "engineer|quality"}],
 "materials": ["..."]}, "summary": "what changed versus the first plan"}"""

BUILD_ALL_TASK = """Build the model according to the APPROVED PLAN by calling tools (geometry first, then materials/colours,
then textures/optimisation). Use report_stage before each phase. Use set_parameters for key dimensions/colours and
'=expr' arguments. Edit the existing model if there is one instead of rebuilding it."""

BUILD_GEOMETRY_TASK = """You are the 3D Engineer: build the GEOMETRY of the APPROVED PLAN with tools (correct dimensions,
topology, booleans, structure, real-world scale). Do not spend time on final materials — the Quality Specialist does
that next. Use report_stage for each phase and set_parameters for key dimensions."""

BUILD_LOOK_TASK = """You are the Texture & Quality Specialist: the geometry is built. Apply PBR materials, colours,
textures, decals/logos and detail finishing according to the APPROVED PLAN and the references. Replace any grey
placeholder (clay) materials. Do not change dimensions."""

FIX_FREE_TASK = """Inspect the TECHNICAL QA and fix the real problems you find with tools. Keep changes minimal and
reversible; explain each fix."""

REVIEW_TASK = """Review the CURRENT MODEL against the brief and the approved plan, using the TECHNICAL QA facts. Work
independently. For every real problem give the object, evidence and an executable fix (one engine tool call).
Rules: if you claim a measured value, cite the QA check id in "qa_check"; anything not in QA is your opinion.
ai_scores are YOUR OPINION (0-100) per category, not measurements. Available fix tools and required args:
{tools}
Reply with ONLY JSON: {{"verdict": "pass|needs_fixes", "ai_scores": {{"category": 0}}, "issues": [{{"severity":
"minor|major|critical", "category": "...", "target": "object name", "problem": "...", "evidence": "...",
"qa_check": "check id or null", "fix": {{"tool": "...", "args": {{}}, "description": "..."}} }}], "summary": "..."}}"""

FIX_CHECK_TASK = """Other agents proposed these fixes. Check each one critically: is the problem real (evidence)? will
the tool call actually fix it without side effects? Reject wrong or harmful fixes. Reply with ONLY JSON:
{"checks": [{"issue_id": "...", "approve": true, "reason": "..."}], "summary": "..."}"""

FIX_DECIDE_TASK = """As Lead Designer approve or reject each proposed fix, taking the cross-checks into account. Prefer
fixes backed by technical tests. Reply with ONLY JSON: {"decisions": [{"issue_id": "...", "decision": "approve|reject",
"reason": "..."}], "summary": "..."}"""

SUMMARY_TASK = """

## YOUR TASK
Write a short final report for the user (in their language): what the team built, which problems were found and
fixed, what remains open, and which checks were NOT verified. Plain text, max 8 lines."""


async def run_team(job: Job, resume: bool = False) -> None:
    cfg = tcfg.load()
    if job.team.get("mode_override"):
        cfg["mode"] = job.team["mode_override"]
    tr = TeamRun(job, cfg)
    try:
        await tr.run(resume)
    except LimitExceeded as e:
        tr.post(None, "system", "error", f"Limit: {e}. Praca zatrzymana — możesz wznowić.")
        job.status = "stopped"
        log(job, str(e), "warn")
    finally:
        job.team["failed"] = sorted(tr.failed)
        tr.publish_state()
