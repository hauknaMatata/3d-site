"""HTTP API + live-build job + LLM agent loop against a mock OpenAI-compatible server."""
import json
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # the MCP session manager allows one lifespan per process
        yield c


def _wait_job(c, pid, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = c.get(f"/api/projects/{pid}/jobs/current").json()
        if j and j["status"] != "running":
            return j
        time.sleep(0.2)
    raise AssertionError("job timeout")


def test_project_upload_job_export(logo_path, client):
    c = client
    if True:
        p = c.post("/api/projects", json={"name": "api", "mode": "cad"}).json()
        a = c.post(f"/api/projects/{p['id']}/attachments", files={"files": ("logo.png", open(logo_path, "rb"), "image/png")}).json()
        bad = c.post(f"/api/projects/{p['id']}/attachments", files={"files": ("x.exe", b"MZ", "application/octet-stream")})
        assert bad.status_code == 400
        j = c.post(f"/api/projects/{p['id']}/jobs", json={"prompt": "Obudowa 80x60x30 mm z 4 otworami i logo na górze",
                                                         "attachment_ids": [a[0]["id"]], "provider_id": "offline"}).json()
        assert j["engine"] == "planner"
        j = _wait_job(c, p["id"])
        assert j["status"] == "done", j
        snap = c.get(f"/api/projects/{p['id']}").json()["snapshot"]
        assert len(snap["objects"]) == 3 and snap["model_url"]
        assert c.get(snap["model_url"]).status_code == 200
        r = c.post(f"/api/projects/{p['id']}/export", json={"format": "3mf"}).json()
        assert r["validation"]["ok"] and c.get(r["url"]).status_code == 200
        assert c.get(f"/api/projects/{p['id']}/versions").json(), "auto version after job"
        assert c.delete(f"/api/projects/{p['id']}").status_code == 409  # needs explicit confirm
        assert c.delete(f"/api/projects/{p['id']}?confirm=true").status_code == 200


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture()
def mock_llm():
    """OpenAI-compatible mock that scripts a tool-using conversation."""
    mock = FastAPI()
    calls = []

    @mock.get("/v1/models")
    def models():
        return {"data": [{"id": "mock-1"}]}

    @mock.post("/v1/chat/completions")
    def chat(body: dict):
        if not body.get("tools"):  # connection test ("ping")
            return {"choices": [{"message": {"role": "assistant", "content": "pong"}, "finish_reason": "stop"}]}
        calls.append(body)
        n = len(calls)
        tool_names = {t["function"]["name"] for t in body.get("tools", [])}
        assert {"create_box", "report_stage", "set_parameters"} <= tool_names
        if n == 1:
            msg = {"role": "assistant", "content": "Plan: box", "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "report_stage", "arguments": json.dumps({"name": "Główna bryła"})}},
                {"id": "b", "type": "function", "function": {"name": "set_parameters", "arguments": json.dumps({"updates": {"H": 12}})}},
                {"id": "c", "type": "function", "function": {"name": "create_box", "arguments": json.dumps(
                    {"name": "Block", "width": 30, "depth": 20, "height": "=H"})}}]}
        elif n == 2:
            # previous tool results must be sent back
            assert any(m.get("role") == "tool" for m in body["messages"])
            msg = {"role": "assistant", "content": None, "tool_calls": [
                {"id": "d", "type": "function", "function": {"name": "fillet", "arguments": json.dumps({"target": "Nope", "radius": 1})}}]}
        elif n == 3:
            errs = [m for m in body["messages"] if m.get("role") == "tool" and "ERROR" in m.get("content", "")]
            assert errs, "engine errors are reported back to the model"
            msg = {"role": "assistant", "content": None, "tool_calls": [
                {"id": "e", "type": "function", "function": {"name": "fillet", "arguments": json.dumps({"target": "Block", "radius": 1, "edges": "|Z"})}}]}
        else:
            msg = {"role": "assistant", "content": "Gotowe — blok 30×20×12 mm."}
        return {"choices": [{"message": msg, "finish_reason": "stop"}], "usage": {}}

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(mock, host="127.0.0.1", port=port, log_level="error"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/v1", calls
    server.should_exit = True


def test_llm_agent_loop_with_mock_provider(mock_llm, client):
    base, calls = mock_llm
    c = client
    if True:
        prov = c.post("/api/providers", json={"kind": "openai_compatible", "name": "Mock", "base_url": base, "default_model": "mock-1"}).json()
        assert prov["has_key"] is False
        t = c.post(f"/api/providers/{prov['id']}/test").json()
        assert t["ok"] and "mock-1" in t["models"]
        p = c.post("/api/projects", json={"name": "llm", "mode": "cad"}).json()
        j = c.post(f"/api/projects/{p['id']}/jobs", json={"prompt": "Zrób blok", "provider_id": prov["id"]}).json()
        assert j["engine"] == "llm"
        j = _wait_job(c, p["id"])
        assert j["status"] == "done", j
        snap = c.get(f"/api/projects/{p['id']}").json()["snapshot"]
        assert [o["tool"] for o in snap["ops"]] == ["create_box", "fillet"]
        assert all(o["source"] == "ai" for o in snap["ops"]) and snap["params"]["H"] == 12
        assert abs(snap["objects"][0]["dims"][2] - 12) < 1e-6
        assert "Główna bryła" in j["stages"]
        msgs = c.get(f"/api/projects/{p['id']}/messages").json()
        assert msgs[-1]["content"].startswith("Gotowe") and msgs[-1]["meta"]["model"] == "mock-1"
        # key never returned in clear text
        assert "api_key" not in json.dumps(c.get("/api/providers").json())
        c.delete(f"/api/providers/{prov['id']}")
