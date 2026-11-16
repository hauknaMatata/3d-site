"""Provider adapter contract + normalised conversation format.

Internal message format (provider-agnostic):
  {"role": "user", "content": [{"type": "text", "text": ...} | {"type": "image", "media_type": ..., "data": b64}]}
  {"role": "assistant", "text": str, "tool_calls": [{"id", "name", "args"}]}
  {"role": "tool", "results": [{"id", "name", "content": str, "is_error": bool}]}
Adding a provider = subclass Provider, implement chat()/list_models(), register in registry.py."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

import httpx


class ProviderError(Exception):
    def __init__(self, msg: str, status: int | None = None, retryable: bool = False):
        super().__init__(msg)
        self.status = status
        self.retryable = retryable or (status in (408, 409, 425, 429, 500, 502, 503, 504, 529))


@dataclass
class ToolDef:
    name: str
    description: str
    schema: dict[str, Any]


@dataclass
class AssistantTurn:
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


class Provider:
    kind = ""
    label = ""
    default_base_url = ""
    needs_key = True
    supports_vision = True
    suggested_models: list[str] = []

    def __init__(self, api_key: str = "", base_url: str = "", timeout: float = 180.0):
        self.api_key = api_key
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.timeout = timeout

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout)

    async def list_models(self) -> list[str]:
        return list(self.suggested_models)

    async def chat(self, model: str, system: str, messages: list[dict], tools: list[ToolDef],
                   max_tokens: int = 4096) -> AssistantTurn:
        raise NotImplementedError

    async def test(self, model: str | None = None) -> dict[str, Any]:
        models = await self.list_models()
        m = model or (models[0] if models else None)
        if not m:
            return {"ok": True, "models": models, "note": "Connected; no model to ping."}
        turn = await self.chat(m, "Reply with the single word: pong", [{"role": "user", "content": [{"type": "text", "text": "ping"}]}], [], 16)
        return {"ok": True, "models": models[:200], "model": m, "reply": turn.text[:80]}

    @staticmethod
    def raise_for(r: httpx.Response, who: str) -> None:
        if r.status_code >= 400:
            body = r.text[:500]
            raise ProviderError(f"{who} HTTP {r.status_code}: {body}", r.status_code)


def sanitize_schema(schema: dict, strict_types: bool = False) -> dict:
    """Make JSON schemas acceptable to strict providers (Gemini): no anyOf, every node typed."""
    s = copy.deepcopy(schema)

    def walk(n: dict) -> dict:
        if "anyOf" in n:
            opts = n.pop("anyOf")
            obj = next((o for o in opts if o.get("type") == "object"), opts[0])
            n.update({k: v for k, v in obj.items() if k not in n})
        if strict_types and "type" not in n:
            n["type"] = "number" if "number" in n.get("description", "") else "string"
        if n.get("type") == "object":
            props = n.get("properties")
            if props:
                n["properties"] = {k: walk(v) for k, v in props.items()}
            elif strict_types:
                n["type"] = "string" if False else "object"
        if n.get("type") == "array":
            n["items"] = walk(n.get("items") or {"type": "number"})
        n.pop("minItems", None) if strict_types else None
        n.pop("maxItems", None) if strict_types else None
        return n

    return walk(s)
