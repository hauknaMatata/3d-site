"""AI TEAM without API keys: agents connect over the real MCP protocol (Streamable HTTP), like Claude Desktop /
Claude Code / Gemini CLI do, and work through team_join → team_wait_for_task → team_run_operation → team_submit.
The server runs in a separate process; the agents' answers come from the scripted brain in test_team."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

import test_team as T

BACKEND = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def server():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    env = {**os.environ, "DATA_DIR": tempfile.mkdtemp(prefix="ai3d-mcp-"), "LIVE_BUILD_STEP_DELAY": "0"}
    env.pop("API_TOKEN", None)
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"],
                            cwd=BACKEND, env=env)
    base = f"http://127.0.0.1:{port}"
    for _ in range(200):
        try:
            if httpx.get(base + "/api/health").status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    yield base
    proc.terminate()
    proc.wait(10)


async def agent(base: str, name: str, persona: str, stop: asyncio.Event, log: list):
    async with streamablehttp_client(base + "/mcp/") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            j = json.loads((await s.call_tool("team_join", {"agent_name": name})).content[0].text)
            assert j["agent_id"], j
            log.append((name, "join", j["roles"]))
            while not stop.is_set():
                res = json.loads((await s.call_tool("team_wait_for_task", {"agent_id": j["agent_id"], "timeout_s": 2})).content[0].text)
                if res["status"] != "task":
                    continue
                text = res["role_instructions"] + "\n" + res["task"]
                if res["kind"] == "build":
                    names = set(res["how_to_answer"].split("allowed tools: ")[1].split(" (plus")[0].split(", ")) | {"set_parameters", "report_stage"}
                    out = T.brain(persona, res["role_instructions"], res["task"], True, False, names)
                    for tool, args in out.get("tools", []):
                        r_ = (await s.call_tool("team_run_operation", {"task_id": res["task_id"], "tool": tool, "args": args})).content[0].text
                        log.append((name, tool, r_[:80]))
                    sub = await s.call_tool("team_submit", {"task_id": res["task_id"], "result": f"{name}: model zbudowany"})
                else:
                    out = T.brain(persona, res["role_instructions"], res["task"], False, False, set())
                    sub = await s.call_tool("team_submit", {"task_id": res["task_id"], "result": out["text"]})
                assert json.loads(sub.content[0].text)["ok"], sub
                log.append((name, res["kind"], res["title"]))


def test_team_over_mcp_without_api_keys(server, logo_path):
    c = httpx.Client(base_url=server, timeout=60)
    assert c.get("/api/providers").json()["providers"] == [], "no API keys / providers configured at all"
    cfg = c.put("/api/team/config", json={"members": [
        {"kind": "mcp", "label": "Claude", "order": 0, "role": "auto", "enabled": True},
        {"kind": "mcp", "label": "Gemini", "order": 1, "role": "auto", "enabled": True}], "mode": "auto", "max_rounds": 2}).json()
    assert [m["kind"] for m in cfg["members"]] == ["mcp", "mcp"]
    p = c.post("/api/projects", json={"name": "mcp-team", "mode": "cad"}).json()
    a = c.post(f"/api/projects/{p['id']}/attachments", files={"files": ("logo.png", open(logo_path, "rb"), "image/png")}).json()[0]
    T.ATT.clear()
    T.ATT.update(a)
    j = c.post(f"/api/projects/{p['id']}/team/jobs", json={"prompt": "Obudowa 60×40×25 mm z logo", "attachment_ids": [a["id"]]})
    assert j.status_code == 200, j.text

    async def main():
        stop = asyncio.Event()
        log: list = []
        agents = [asyncio.create_task(agent(server, "Claude", "claude", stop, log)),
                  asyncio.create_task(agent(server, "Gemini", "gemini", stop, log))]
        t0 = time.time()
        job = None
        async with httpx.AsyncClient(base_url=server) as ac:
            while time.time() - t0 < 180:
                job = (await ac.get(f"/api/projects/{p['id']}/jobs/current")).json()
                if job and job["status"] != "running":
                    break
                await asyncio.sleep(0.3)
        stop.set()
        await asyncio.gather(*agents)
        return job, log

    job, log = asyncio.run(main())
    assert job["status"] == "done", job
    kinds = {(n, k) for n, k, *_ in log}
    assert ("Claude", "json") in kinds and ("Gemini", "build") in kinds
    snap = c.get(f"/api/projects/{p['id']}").json()["snapshot"]
    assert {"Base"} <= {o["name"] for o in snap["objects"]}
    assert {o["author"] for o in snap["ops"] if o["source"] == "ai"} >= {"Gemini"}
    msgs = c.get(f"/api/projects/{p['id']}/team/messages").json()
    assert {m["agent_name"] for m in msgs if m["kind"] in ("plan", "critique", "debate", "review")} == {"Claude", "Gemini"}
    issues = c.get(f"/api/projects/{p['id']}/team/issues").json()
    assert any(i["status"] == "applied" and i["backed_by_test"] for i in issues)
    agents_state = c.get("/api/team/mcp/agents").json()
    assert all(a_["connected"] for a_ in agents_state)
