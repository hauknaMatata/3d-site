"""LLM calls for agents: retries with backoff, usage accounting, limits, JSON extraction."""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import httpx

from .. import db
from ..ai import service
from ..ai.providers.base import AssistantTurn, ProviderError, ToolDef


class LimitExceeded(Exception):
    pass


class AgentDown(Exception):
    """Agent failed after all retries — orchestrator re-routes its role."""


def norm_usage(u: dict | None) -> tuple[int, int]:
    u = u or {}
    tin = u.get("input_tokens") or u.get("prompt_tokens") or u.get("promptTokenCount") or u.get("prompt_eval_count") or 0
    tout = u.get("output_tokens") or u.get("completion_tokens") or u.get("candidatesTokenCount") or u.get("eval_count") or 0
    return int(tin or 0), int(tout or 0)


def record_usage(provider_id: str, model: str, tin: int, tout: int, ok: bool, err: str = "", latency_ms: int = 0,
                 retries: int = 0) -> None:
    key = f"{provider_id}:{model}"
    with db.tx() as c:
        c.execute("INSERT OR IGNORE INTO agent_usage(key,provider_id,model,updated) VALUES(?,?,?,?)", (key, provider_id, model, db.now()))
        c.execute("UPDATE agent_usage SET calls=calls+1, tokens_in=tokens_in+?, tokens_out=tokens_out+?, errors=errors+?, "
                  "retries=retries+?, last_error=CASE WHEN ?='' THEN last_error ELSE ? END, "
                  "last_ok=CASE WHEN ? THEN ? ELSE last_ok END, last_latency_ms=?, updated=? WHERE key=?",
                  (tin, tout, 0 if ok else 1, retries, err, err[:500], 1 if ok else 0, db.now(), latency_ms, db.now(), key))


class Budget:
    def __init__(self, cfg: dict):
        self.max_tokens = cfg.get("max_tokens")
        self.max_seconds = cfg.get("max_seconds")
        self.max_cost = cfg.get("max_cost_usd")
        self.t0 = time.time()
        self.tokens = 0
        self.cost = 0.0

    def check(self) -> None:
        if self.max_tokens and self.tokens >= self.max_tokens:
            raise LimitExceeded(f"Token limit reached ({self.tokens}/{self.max_tokens})")
        if self.max_seconds and time.time() - self.t0 >= self.max_seconds:
            raise LimitExceeded(f"Time limit reached ({int(time.time() - self.t0)} s)")
        if self.max_cost and self.cost >= self.max_cost:
            raise LimitExceeded(f"Cost limit reached (${self.cost:.3f})")

    def add(self, member: dict, tin: int, tout: int) -> None:
        self.tokens += tin + tout
        if member.get("price_in") is not None and member.get("price_out") is not None:
            self.cost += tin / 1e6 * float(member["price_in"]) + tout / 1e6 * float(member["price_out"])


async def call(member: dict, system: str, messages: list[dict], tools: list[ToolDef], budget: Budget, retries: int,
               state: dict, max_tokens: int = 4096) -> AssistantTurn:
    budget.check()
    provider, model, _ = service.resolve("modeling", member["provider_id"], member.get("model") or None)  # type: ignore[misc]
    member["model_resolved"] = model
    last = ""
    for attempt in range(retries + 1):
        t0 = time.time()
        try:
            turn = await provider.chat(model, system, messages, tools, max_tokens)
            tin, tout = norm_usage(turn.usage)
            budget.add(member, tin, tout)
            state["tokens_in"] = state.get("tokens_in", 0) + tin
            state["tokens_out"] = state.get("tokens_out", 0) + tout
            state["calls"] = state.get("calls", 0) + 1
            record_usage(member["provider_id"], model, tin, tout, True, "", int((time.time() - t0) * 1000), attempt)
            return turn
        except (ProviderError, httpx.HTTPError, asyncio.TimeoutError) as e:
            retryable = getattr(e, "retryable", True) if isinstance(e, ProviderError) else True
            last = f"{type(e).__name__}: {e}"[:400]
            state["errors"] = state.get("errors", 0) + 1
            state["last_error"] = last
            record_usage(member["provider_id"], model, 0, 0, False, last, int((time.time() - t0) * 1000))
            if not retryable or attempt == retries:
                break
            await asyncio.sleep(min(20, 1.5 * 2 ** attempt))
    raise AgentDown(last)


_JSON = re.compile(r"\{.*\}", re.S)


def extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    for cand in (t, *(m.group(0) for m in [_JSON.search(t)] if m)):
        try:
            v = json.loads(cand)
            if isinstance(v, dict):
                return v
        except json.JSONDecodeError:
            continue
    return None


async def call_json(member: dict, system: str, user: str, budget: Budget, retries: int, state: dict,
                    images: list[dict] | None = None, max_tokens: int = 4096) -> tuple[dict, AssistantTurn]:
    msgs = [{"role": "user", "content": (images or []) + [{"type": "text", "text": user}]}]
    turn = await call(member, system, msgs, [], budget, retries, state, max_tokens)
    data = extract_json(turn.text)
    if data is None:  # one repair attempt
        msgs += [{"role": "assistant", "text": turn.text, "tool_calls": []},
                 {"role": "user", "content": [{"type": "text", "text": "Your answer was not valid JSON. Reply again with ONLY the JSON object."}]}]
        turn = await call(member, system, msgs, [], budget, retries, state, max_tokens)
        data = extract_json(turn.text)
    if data is None:
        raise AgentDown(f"invalid JSON from {member.get('model_resolved')}: {turn.text[:200]}")
    return data, turn
