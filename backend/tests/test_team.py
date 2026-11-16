"""AI TEAM scenarios A–F.

The real Anthropic / OpenAI / Gemini services are not reachable from CI, so a local server emulates the three
vendor wire formats (Anthropic Messages, OpenAI Chat Completions, Gemini generateContent). The production
adapters, orchestrator, engine, QA and fix pipeline run unmodified; only the "model" answers are scripted
and depend on what the request actually contains (phase, QA facts, issue ids)."""
from __future__ import annotations

import json
import re
import socket
import threading
import time
import uuid

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.main import app

# ----------------------------------------------------------------------------- scripted "brain"
LOG: list[dict] = []


def brain(agent: str, system: str, text: str, has_tools: bool, has_tool_results: bool, tool_names: set[str]) -> dict:
    """Returns {"text": str} or {"tools": [(name, args)]} depending on the phase found in the request."""
    LOG.append({"agent": agent, "task": text.split("## YOUR TASK")[-1][:80]})
    if "Reply with the single word" in system:
        return {"text": "pong"}
    task = text.split("## YOUR TASK")[-1]
    if "write the modeling plan" in task:
        return {"text": json.dumps({"concept": f"{agent}: obudowa z logo", "parameters": {"W": 60, "D": 40, "H": 25},
                                    "steps": [{"stage": "Korpus", "description": "box + shell", "owner": "engineer"},
                                              {"stage": "Logo", "description": "emboss", "owner": "quality"}],
                                    "materials": ["PLA"], "risks": ["cienkie logo"], "summary": f"Plan od {agent}"})}
    if "Critically review the Lead Designer" in task:
        opinions = {"claude": ("approve", "Proporcje dobre, ale logo może być za cienkie do druku."),
                    "gpt": ("changes", "Zwiększ zaokrąglenia — ostre narożniki pękają przy druku."),
                    "gemini": ("changes", "Proporcje karoserii/obudowy są złe — obudowa powinna być wyższa.")}
        v, p = opinions[agent]
        return {"text": json.dumps({"verdict": v, "issues": [{"id": "I1", "severity": "major", "area": "geometry", "problem": p,
                                                              "proposal": p}], "position": p, "confidence": 70})}
    if "take a stance" in task:
        # gpt and gemini genuinely disagree about the proportions; claude partially agrees with both
        st = {"gpt": [{"item": "gemini:I1", "stance": "disagree", "argument": "Wymiary 60×40×25 wynikają z briefu — nie zmieniamy."}],
              "gemini": [{"item": "gpt:I1", "stance": "agree", "argument": "Zaokrąglenia pomogą."}],
              "claude": [{"item": "gemini:I1", "stance": "partial", "argument": "Wysokość zgodna z briefem; detal OK."}]}
        return {"text": json.dumps({"stances": st.get(agent, []), "position": f"{agent} stance"})}
    if "You are the Lead Designer: weigh" in task:
        return {"text": json.dumps({"decisions": [{"item": "gpt:I1", "decision": "accept", "reason": "druk"},
                                                  {"item": "gemini:I1", "decision": "reject", "reason": "Lead: wymiary zostają zgodne z briefem"}],
                                    "conflicts_resolved": [{"topic": "proporcje", "sides": "gpt vs gemini", "ruling": "wymiary bez zmian"}],
                                    "final_plan": {"concept": "obudowa 60×40×25 z logo", "parameters": {"W": 60, "D": 40, "H": 25}},
                                    "summary": "Przyjmuję zaokrąglenia, odrzucam zmianę proporcji."})}
    if has_tools and not has_tool_results:
        if "3D Engineer: build the GEOMETRY" in task or "Build the model according" in task:
            calls = [("report_stage", {"name": "Korpus"}),
                     ("set_parameters", {"updates": {"W": 60, "D": 40, "H": 25, "logo_depth": 0.6}}),
                     ("create_box", {"name": "Base", "width": "=W", "depth": "=D", "height": "=H"}),
                     ("fillet", {"target": "Base", "radius": 2, "edges": "|Z"}),
                     ("shell", {"target": "Base", "thickness": 2, "open_face": "<Z"})]
            if "Build the model according" in task:  # single agent also does the look
                calls += [("apply_image", {"target": "Base", "attachment_id": ATT["id"], "depth": "=logo_depth", "width": 30}),
                          ("set_color", {"target": "Base", "color": "#c0121c"})]
            return {"tools": [c for c in calls if c[0] in tool_names]}
        if "Texture & Quality Specialist: the geometry is built" in task:
            return {"tools": [c for c in [("apply_image", {"target": "Base", "attachment_id": ATT["id"], "depth": "=logo_depth", "width": 30}),
                                          ("set_color", {"target": "Base", "color": "#c0121c"}),
                                          ("create_box", {"name": "Forbidden", "width": 1, "depth": 1, "height": 1})]]}  # not allowed for QA role
    if has_tools and has_tool_results:
        return {"text": f"{agent}: etap zakończony."}
    if "Review the CURRENT MODEL" in task:
        issues = []
        if "[WARN] print.wall_thickness" in text and "logo_depth" in text:
            issues.append({"severity": "minor", "category": "Druk 3D", "target": "Logo_1",
                           "problem": "Logo ma 0.6 mm — poniżej 0.8 mm (2 obwody dyszy 0.4).", "evidence": "QA print.wall_thickness",
                           "qa_check": "print.wall_thickness",
                           "fix": {"tool": "set_parameters", "args": {"updates": {"logo_depth": 1.0}}, "description": "Logo 1.0 mm"}})
        if agent == "gemini":  # an opinion-only issue with an invalid fix → must be rejected by validation
            issues.append({"severity": "minor", "category": "Wygląd", "target": "Nope", "problem": "Brak faz.", "evidence": "opinia",
                           "qa_check": "made.up.check", "fix": {"tool": "chamfer", "args": {"target": "Nope", "distance": 0.4}}})
        return {"text": json.dumps({"verdict": "needs_fixes" if issues else "pass", "ai_scores": {"Geometria": 80, "Druk 3D": 70},
                                    "issues": issues, "summary": f"{agent}: {len(issues)} uwag"})}
    if "Other agents proposed these fixes" in task:
        ids = re.findall(r'"issue_id": "([0-9a-f]+)"', text)
        return {"text": json.dumps({"checks": [{"issue_id": i, "approve": True, "reason": "Potwierdzone testem QA"} for i in ids],
                                    "summary": "ok"})}
    if "approve or reject each proposed fix" in task:
        ids = re.findall(r'"issue_id": "([0-9a-f]+)"', text)
        return {"text": json.dumps({"decisions": [{"issue_id": i, "decision": "approve", "reason": "oparte na teście"} for i in ids]})}
    if "final report" in task:
        return {"text": f"[{agent}] Zbudowano obudowę, poprawiono grubość logo. Nawisy niesprawdzone."}
    return {"text": "{}"}


ATT: dict = {}
DOWN: set[str] = set()


def _agent(model: str) -> str:
    return "claude" if "claude" in model else "gpt" if "gpt" in model else "gemini"


def make_mock() -> FastAPI:
    mock = FastAPI()

    @mock.post("/anthropic/v1/messages")
    async def anthropic(req: Request):
        b = await req.json()
        agent = _agent(b["model"])
        if agent in DOWN:
            return _err()
        text = "\n".join(c.get("text", "") for m in b["messages"] if m["role"] == "user" for c in m["content"] if c.get("type") == "text")
        tr = any(c.get("type") == "tool_result" for m in b["messages"] if m["role"] == "user" for c in m["content"])
        r = brain(agent, b.get("system", ""), text, bool(b.get("tools")), tr, {t["name"] for t in b.get("tools", [])})
        content = [{"type": "text", "text": r["text"]}] if "text" in r else \
            [{"type": "tool_use", "id": "tu_" + uuid.uuid4().hex[:8], "name": n, "input": a} for n, a in r["tools"]]
        return {"content": content, "stop_reason": "end_turn", "usage": {"input_tokens": len(text) // 4, "output_tokens": 50}}

    @mock.get("/anthropic/v1/models")
    def amodels():
        return {"data": [{"id": "claude-mock"}]}

    @mock.post("/openai/v1/chat/completions")
    async def openai(req: Request):
        b = await req.json()
        agent = _agent(b["model"])
        if agent in DOWN:
            return _err()
        sysm = next((m["content"] for m in b["messages"] if m["role"] == "system"), "")
        text = "\n".join(p.get("text", "") for m in b["messages"] if m["role"] == "user" for p in m["content"])
        tr = any(m["role"] == "tool" for m in b["messages"])
        r = brain(agent, sysm, text, bool(b.get("tools")), tr, {t["function"]["name"] for t in b.get("tools", [])})
        msg = {"role": "assistant", "content": r.get("text")}
        if "tools" in r:
            msg["tool_calls"] = [{"id": "c_" + uuid.uuid4().hex[:8], "type": "function",
                                  "function": {"name": n, "arguments": json.dumps(a)}} for n, a in r["tools"]]
        return {"choices": [{"message": msg, "finish_reason": "stop"}], "usage": {"prompt_tokens": len(text) // 4, "completion_tokens": 40}}

    @mock.get("/openai/v1/models")
    def omodels():
        return {"data": [{"id": "gpt-mock"}]}

    @mock.post("/gemini/v1beta/models/{model}:generateContent")
    async def gemini(model: str, req: Request):
        b = await req.json()
        agent = _agent(model)
        if agent in DOWN:
            return _err()
        sysm = " ".join(p["text"] for p in (b.get("systemInstruction") or {}).get("parts", []))
        text = "\n".join(p.get("text", "") for c in b["contents"] if c["role"] == "user" for p in c["parts"])
        tr = any("functionResponse" in p for c in b["contents"] for p in c["parts"])
        names = {f["name"] for t in b.get("tools", []) for f in t["functionDeclarations"]}
        r = brain(agent, sysm, text, bool(b.get("tools")), tr, names)
        parts = [{"text": r["text"]}] if "text" in r else [{"functionCall": {"name": n, "args": a}} for n, a in r["tools"]]
        return {"candidates": [{"content": {"parts": parts}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": len(text) // 4, "candidatesTokenCount": 30}}

    @mock.get("/gemini/v1beta/models")
    def gmodels():
        return {"models": [{"name": "models/gemini-mock", "supportedGenerationMethods": ["generateContent"]}]}

    return mock


def _err():
    from fastapi.responses import JSONResponse

    return JSONResponse({"error": "overloaded"}, 503)


@pytest.fixture(scope="module")
def env(logo_path, client):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(uvicorn.Config(make_mock(), host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"
    c = client
    if True:
        provs = {
            "claude": c.post("/api/providers", json={"kind": "anthropic", "name": "Claude", "api_key": "sk-ant-test", "base_url": base + "/anthropic",
                                                     "default_model": "claude-mock"}).json(),
            "gpt": c.post("/api/providers", json={"kind": "openai", "name": "ChatGPT", "api_key": "sk-test", "base_url": base + "/openai/v1",
                                                  "default_model": "gpt-mock"}).json(),
            "gemini": c.post("/api/providers", json={"kind": "gemini", "name": "Gemini", "api_key": "g-test", "base_url": base + "/gemini/v1beta",
                                                     "default_model": "gemini-mock"}).json(),
        }
        for p in provs.values():
            assert c.post(f"/api/providers/{p['id']}/test").json()["ok"]
        yield c, provs, logo_path
        server.should_exit = True


def setup_team(c, provs, names, mode="auto", **extra):
    members = [{"provider_id": provs[n]["id"], "model": provs[n]["default_model"], "label": n, "enabled": True, "order": i, "role": "auto"}
               for i, n in enumerate(names)]
    cfg = c.put("/api/team/config", json={"members": members, "mode": mode, "debate": True, "max_rounds": 2, "retries": 1, **extra}).json()
    return cfg


def run_job(c, logo_path, mode=None, prompt="Obudowa 60×40×25 mm z logo na górze", wait=True):
    p = c.post("/api/projects", json={"name": "team", "mode": "cad"}).json()
    a = c.post(f"/api/projects/{p['id']}/attachments", files={"files": ("logo.png", open(logo_path, "rb"), "image/png")}).json()[0]
    ATT.clear()
    ATT.update(a)
    j = c.post(f"/api/projects/{p['id']}/team/jobs", json={"prompt": prompt, "attachment_ids": [a["id"]], "mode": mode})
    assert j.status_code == 200, j.text
    if not wait:
        return p["id"]
    return p["id"], wait_done(c, p["id"])


def wait_done(c, pid, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = c.get(f"/api/projects/{pid}/jobs/current").json()
        if j and j["status"] != "running":
            return j
        time.sleep(0.25)
    raise AssertionError("team job timeout")


def msgs(c, pid):
    return c.get(f"/api/projects/{pid}/team/messages").json()


# ----------------------------------------------------------------------------- scenarios
def test_A_single_claude_builds_and_self_checks(env):
    c, provs, logo = env
    cfg = setup_team(c, provs, ["claude"])
    assert cfg["effective_roles"][cfg["members"][0]["id"]] == ["lead", "engineer", "quality"]
    pid, job = run_job(c, logo)
    assert job["status"] == "done", job
    m = msgs(c, pid)
    kinds = [x["kind"] for x in m]
    assert "plan" in kinds and "critique" in kinds and "review" in kinds and "summary" in kinds
    assert all(x["agent_name"] in ("claude", "System") for x in m)
    snap = c.get(f"/api/projects/{pid}").json()["snapshot"]
    assert {o["author"] for o in snap["ops"] if o["source"] == "ai"} == {"claude"}
    issues = c.get(f"/api/projects/{pid}/team/issues").json()
    applied = [i for i in issues if i["status"] == "applied"]
    assert applied and applied[0]["backed_by_test"] and applied[0]["qa_check"] == "print.wall_thickness"


def test_B_chatgpt_and_gemini_review_each_other(env):
    c, provs, logo = env
    cfg = setup_team(c, provs, ["gpt", "gemini"])
    roles = cfg["effective_roles"]
    ids = [m["id"] for m in cfg["members"]]
    assert roles[ids[0]] == ["lead"] and set(roles[ids[1]]) == {"engineer", "quality"}
    pid, job = run_job(c, logo)
    assert job["status"] == "done", job
    m = msgs(c, pid)
    assert any(x["kind"] == "critique" and x["agent_name"] == "gemini" for x in m), "Gemini reviews ChatGPT's plan"
    assert any(x["kind"] == "plan" and x["agent_name"] == "gpt" for x in m)
    checks = [x for x in m if x["kind"] == "fix_check"]
    issues = c.get(f"/api/projects/{pid}/team/issues").json()
    for i in issues:
        for r in i["reviews"] or []:
            assert r["by"] != i["author"], "fixes are verified by a different agent"
    assert checks
    # the made-up QA claim and invalid fix from gemini were rejected by engine validation
    bogus = [i for i in issues if i["target"] == "Nope"]
    assert bogus and all(i["status"] == "invalid" and not i["backed_by_test"] for i in bogus)


def test_C_three_agents_collaborate(env):
    c, provs, logo = env
    cfg = setup_team(c, provs, ["claude", "gpt", "gemini"])
    pid, job = run_job(c, logo)
    assert job["status"] == "done", job
    m = msgs(c, pid)
    assert {x["agent_name"] for x in m if x["kind"] == "debate"} == {"claude", "gpt", "gemini"}
    snap = c.get(f"/api/projects/{pid}").json()["snapshot"]
    authors = {o["author"] for o in snap["ops"] if o["source"] == "ai"}
    assert {"gpt", "gemini"} <= authors, "engineer builds geometry, quality specialist does materials"
    geo = [o for o in snap["ops"] if o["tool"] in ("create_box", "shell")]
    look = [o for o in snap["ops"] if o["tool"] in ("set_color", "apply_image")]
    assert all(o["author"] == "gpt" for o in geo) and all(o["author"] == "gemini" for o in look)
    assert not any(o["label"] == "create box" and o["author"] == "gemini" for o in snap["ops"]), "QA role cannot add geometry"
    assert job["team"]["comparisons"], "before/after comparison recorded"
    cmp = job["team"]["comparisons"][0]
    assert cmp["before"]["checks"]["print.wall_thickness"]["status"] == "warn"
    assert cmp["after"]["checks"]["print.wall_thickness"]["status"] == "pass"


def test_D_agent_down_others_continue(env):
    c, provs, logo = env
    setup_team(c, provs, ["claude", "gpt", "gemini"])
    DOWN.add("gemini")
    try:
        pid, job = run_job(c, logo)
    finally:
        DOWN.discard("gemini")
    assert job["status"] == "done", job
    gem = next(m for m in job["team"]["members"] if m["name"] == "gemini")
    assert gem["failed"] and gem["status"] == "error"
    m = msgs(c, pid)
    assert any(x["kind"] == "error" and "gemini" in x["content"] for x in m)
    snap = c.get(f"/api/projects/{pid}").json()["snapshot"]
    assert snap["objects"], "remaining agents still built the model"
    usage = {u["model"]: u for u in c.get("/api/team/usage").json()}
    assert usage["gemini-mock"]["errors"] >= 1


def test_E_conflicting_opinions_lead_decides(env):
    c, provs, logo = env
    setup_team(c, provs, ["claude", "gpt", "gemini"])
    pid, job = run_job(c, logo)
    conflicts = job["team"]["conflicts"]
    assert any(x["agent"] == "gpt" and x["stance"] == "disagree" for x in conflicts)
    dec = [x for x in msgs(c, pid) if x["kind"] == "decision" and x["phase"] == "decision"]
    assert dec and dec[0]["agent_name"] == "claude", "the Lead Designer decides"
    d = dec[0]["data"]
    assert any(x["decision"] == "reject" for x in d["decisions"]) and d["conflicts_resolved"]


def test_F_geometry_error_fixed_rechecked_and_reversible(env):
    c, provs, logo = env
    setup_team(c, provs, ["gpt", "gemini"])
    pid = run_job(c, logo, mode="review", wait=False)
    # REVIEW MODE: the fix waits for the user
    t0 = time.time()
    pending = []
    while time.time() - t0 < 60 and not pending:
        pending = [i for i in c.get(f"/api/projects/{pid}/team/issues").json() if i["status"] == "pending_user"]
        time.sleep(0.2)
    assert pending, "fix waits for user approval in REVIEW mode"
    lid_before = c.get(f"/api/projects/{pid}").json()["snapshot"]["params"]["logo_depth"]
    assert lid_before == 0.6
    assert len(pending) == 1, "identical fixes from two agents are merged into one issue"
    r = c.post(f"/api/projects/{pid}/team/issues/{pending[0]['id']}/decide", json={"approve": True})
    assert r.status_code == 200
    assert any(x["approve"] and "ten sam problem" in x["reason"] for x in r.json()["reviews"]), "second agent confirmed it"
    job = wait_done(c, pid)
    assert job["status"] == "done", job
    snap = c.get(f"/api/projects/{pid}").json()["snapshot"]
    assert snap["params"]["logo_depth"] == 1.0
    qa = c.get(f"/api/projects/{pid}/team/qa").json()
    assert qa[0]["report"]["checks"] and any(r["stage"] == "po poprawkach" for r in qa)
    after = next(r for r in qa if r["stage"] == "po poprawkach")["report"]
    assert next(x for x in after["checks"] if x["id"] == "print.wall_thickness")["status"] == "pass"
    issue = next(i for i in c.get(f"/api/projects/{pid}/team/issues").json() if i["id"] == pending[0]["id"])
    assert issue["status"] == "applied" and issue["version_before"]
    # revert the fix → original value restored, nothing lost
    rv = c.post(f"/api/projects/{pid}/team/issues/{issue['id']}/revert")
    assert rv.status_code == 200 and rv.json()["snapshot"]["params"]["logo_depth"] == 0.6
    # a version saved before fixes exists
    assert any(v["label"].startswith("Przed poprawkami") for v in c.get(f"/api/projects/{pid}/versions").json())


def test_manual_mode_single_task(env):
    c, provs, logo = env
    cfg = setup_team(c, provs, ["claude", "gpt"], mode="manual")
    p = c.post("/api/projects", json={"name": "manual", "mode": "cad"}).json()
    member = cfg["members"][1]["id"]
    j = c.post(f"/api/projects/{p['id']}/team/jobs", json={"prompt": "Zrób przegląd", "member_id": member, "task": "review"})
    assert j.status_code == 200
    job = wait_done(c, p["id"])
    assert job["status"] == "done"
    m = msgs(c, p["id"])
    assert m and all(x["agent_name"] in ("gpt", "System") for x in m)


def test_limits_stop_team(env):
    c, provs, logo = env
    setup_team(c, provs, ["claude"], max_tokens=50)
    pid, job = run_job(c, logo)
    assert job["status"] == "stopped"
    assert any("Limit" in x["content"] for x in msgs(c, pid))
