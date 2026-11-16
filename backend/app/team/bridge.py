"""MCP BRIDGE — team members without API keys.

Instead of the backend calling a vendor API, an AI app the user is logged into (Claude Desktop / Claude Code,
Gemini CLI, ChatGPT with MCP connectors…) connects to this app's MCP server and acts as a team member:

    team_join(name) → loop { team_wait_for_task → do it (team_run_operation for modeling) → team_submit }

The orchestrator puts tasks into a per-agent queue and awaits the result, so the whole team workflow
(plan, critique, debate, decisions, build, QA, fixes) is identical for API and MCP agents."""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .llm import AgentDown, extract_json

CONNECTED_WINDOW_S = 120


@dataclass
class BridgeTask:
    id: str
    member_id: str
    project_id: str
    job_id: str
    kind: str  # json | build | text
    title: str
    system: str
    body: str
    allowed_tools: list[str] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    picked: float | None = None
    future: asyncio.Future | None = None
    run_op: Callable[[str, dict], Awaitable[Any]] | None = None
    ops: int = 0
    done: bool = False

    def payload(self) -> dict[str, Any]:
        fmt = {"json": "Reply by calling team_submit(task_id, result) where result is ONLY the JSON object described in YOUR TASK.",
               "build": "Modify the model ONLY by calling team_run_operation(task_id, tool, args) — one call per operation, "
                        f"allowed tools: {', '.join(self.allowed_tools)} (plus set_parameters with args {{updates:{{...}}}}). "
                        "Read results/errors and correct yourself. When finished call team_submit(task_id, short summary).",
               "text": "Reply by calling team_submit(task_id, result) with plain text."}[self.kind]
        return {"status": "task", "task_id": self.id, "project_id": self.project_id, "kind": self.kind, "title": self.title,
                "how_to_answer": fmt, "role_instructions": self.system, "task": self.body}


class Broker:
    def __init__(self) -> None:
        self.tasks: dict[str, BridgeTask] = {}
        self.queues: dict[str, list[str]] = {}
        self.events: dict[str, asyncio.Event] = {}
        self.seen: dict[str, float] = {}

    # ---------------------------------------------------------------- agent side (MCP tools)
    def touch(self, member_id: str) -> None:
        self.seen[member_id] = time.time()

    def connected(self, member_id: str) -> bool:
        return time.time() - self.seen.get(member_id, 0) < CONNECTED_WINDOW_S

    def _event(self, member_id: str) -> asyncio.Event:
        return self.events.setdefault(member_id, asyncio.Event())

    async def next_task(self, member_id: str, timeout: float) -> BridgeTask | None:
        self.touch(member_id)
        end = time.time() + timeout
        while True:
            # a task already picked but not finished is handed out again (agent may have lost context)
            for tid in self.queues.get(member_id, []):
                t = self.tasks.get(tid)
                if t and not t.done:
                    t.picked = t.picked or time.time()
                    return t
            left = end - time.time()
            if left <= 0:
                return None
            ev = self._event(member_id)
            ev.clear()
            try:
                await asyncio.wait_for(ev.wait(), min(left, 5))
            except asyncio.TimeoutError:
                self.touch(member_id)

    def submit(self, task_id: str, result: str) -> tuple[bool, str]:
        t = self.tasks.get(task_id)
        if not t or t.done:
            return False, "Unknown or already finished task_id."
        self.touch(t.member_id)
        if t.kind == "json":
            data = extract_json(result if isinstance(result, str) else str(result))
            if data is None:
                return False, "Result is not a valid JSON object — submit again with ONLY the JSON."
            value: Any = data
        else:
            value = str(result)
        t.done = True
        if t.future and not t.future.done():
            t.future.set_result(value)
        self._remove(t)
        return True, "accepted"

    def _remove(self, t: BridgeTask) -> None:
        q = self.queues.get(t.member_id, [])
        if t.id in q:
            q.remove(t.id)

    # ---------------------------------------------------------------- orchestrator side
    async def request(self, member: dict, project_id: str, job_id: str, kind: str, title: str, system: str, body: str,
                      timeout: float, allowed_tools: list[str] | None = None,
                      run_op: Callable[[str, dict], Awaitable[Any]] | None = None, on_wait: Callable[[bool], None] | None = None) -> Any:
        t = BridgeTask(uuid.uuid4().hex[:12], member["id"], project_id, job_id, kind, title, system, body, allowed_tools or [],
                       run_op=run_op)
        t.future = asyncio.get_running_loop().create_future()
        self.tasks[t.id] = t
        self.queues.setdefault(member["id"], []).append(t.id)
        self._event(member["id"]).set()
        t0 = time.time()
        try:
            while True:
                try:
                    return await asyncio.wait_for(asyncio.shield(t.future), 3)
                except asyncio.TimeoutError:
                    if on_wait:
                        on_wait(t.picked is not None)
                    if time.time() - t0 > timeout:
                        raise AgentDown(f"MCP agent '{member.get('label')}' did not finish the task within {int(timeout)} s "
                                        f"({'picked up' if t.picked else 'never picked up — is the AI app connected and looping?'})") from None
        finally:
            t.done = True
            self._remove(t)
            self.tasks.pop(t.id, None)

    def cancel_job(self, job_id: str) -> None:
        for t in list(self.tasks.values()):
            if t.job_id == job_id and t.future and not t.future.done():
                t.future.cancel()

    def pending_for(self, member_id: str) -> list[BridgeTask]:
        return [self.tasks[i] for i in self.queues.get(member_id, []) if i in self.tasks]


broker = Broker()
