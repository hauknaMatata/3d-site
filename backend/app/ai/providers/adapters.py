"""Concrete adapters: Anthropic, OpenAI (+ any OpenAI-compatible API), Google Gemini, Ollama.
All talk to the vendors' public REST APIs directly (no MCP involved)."""
from __future__ import annotations

import json
import uuid
from typing import Any

from .base import AssistantTurn, Provider, ProviderError, ToolDef, sanitize_schema


def _text_of(content: list[dict]) -> str:
    return "\n".join(c["text"] for c in content if c.get("type") == "text")


# ------------------------------------------------------------------------------------------ Anthropic
class AnthropicProvider(Provider):
    kind = "anthropic"
    label = "Anthropic Claude"
    default_base_url = "https://api.anthropic.com"
    suggested_models = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]

    def _headers(self) -> dict:
        return {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}

    async def list_models(self) -> list[str]:
        async with self.client() as c:
            r = await c.get(f"{self.base_url}/v1/models?limit=100", headers=self._headers())
            self.raise_for(r, "Anthropic")
            return [m["id"] for m in r.json().get("data", [])] or self.suggested_models

    @staticmethod
    def convert(messages: list[dict]) -> list[dict]:
        out = []
        for m in messages:
            if m["role"] == "user":
                blocks = []
                for c in m["content"]:
                    if c["type"] == "text":
                        blocks.append({"type": "text", "text": c["text"]})
                    elif c["type"] == "image":
                        blocks.append({"type": "image", "source": {"type": "base64", "media_type": c["media_type"], "data": c["data"]}})
                out.append({"role": "user", "content": blocks})
            elif m["role"] == "assistant":
                blocks = [{"type": "text", "text": m["text"]}] if m.get("text") else []
                blocks += [{"type": "tool_use", "id": t["id"], "name": t["name"], "input": t["args"]} for t in m.get("tool_calls", [])]
                out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "…"}]})
            elif m["role"] == "tool":
                out.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"], "is_error": bool(r.get("is_error"))}
                    for r in m["results"]]})
        return out

    async def chat(self, model, system, messages, tools, max_tokens=4096) -> AssistantTurn:
        body: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "system": system, "messages": self.convert(messages)}
        if tools:
            body["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.schema} for t in tools]
        async with self.client() as c:
            r = await c.post(f"{self.base_url}/v1/messages", headers=self._headers(), json=body)
        self.raise_for(r, "Anthropic")
        j = r.json()
        turn = AssistantTurn(stop_reason=j.get("stop_reason", ""), usage=j.get("usage", {}))
        for b in j.get("content", []):
            if b["type"] == "text":
                turn.text += b["text"]
            elif b["type"] == "tool_use":
                turn.tool_calls.append({"id": b["id"], "name": b["name"], "args": b.get("input") or {}})
        return turn


# ------------------------------------------------------------------------------------------ OpenAI (+compatible)
class OpenAIProvider(Provider):
    kind = "openai"
    label = "OpenAI ChatGPT"
    default_base_url = "https://api.openai.com/v1"
    suggested_models = ["gpt-4.1", "gpt-4o", "gpt-4o-mini"]

    def _headers(self) -> dict:
        h = {"content-type": "application/json"}
        if self.api_key:
            h["authorization"] = f"Bearer {self.api_key}"
        return h

    async def list_models(self) -> list[str]:
        async with self.client() as c:
            r = await c.get(f"{self.base_url}/models", headers=self._headers())
            self.raise_for(r, self.label)
            return sorted(m["id"] for m in r.json().get("data", [])) or self.suggested_models

    @staticmethod
    def convert(system: str, messages: list[dict]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}] if system else []
        for m in messages:
            if m["role"] == "user":
                parts = []
                for c in m["content"]:
                    if c["type"] == "text":
                        parts.append({"type": "text", "text": c["text"]})
                    elif c["type"] == "image":
                        parts.append({"type": "image_url", "image_url": {"url": f"data:{c['media_type']};base64,{c['data']}"}})
                out.append({"role": "user", "content": parts})
            elif m["role"] == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": m.get("text") or None}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [{"id": t["id"], "type": "function",
                                          "function": {"name": t["name"], "arguments": json.dumps(t["args"])}}
                                         for t in m["tool_calls"]]
                out.append(msg)
            elif m["role"] == "tool":
                for r in m["results"]:
                    out.append({"role": "tool", "tool_call_id": r["id"], "content": r["content"]})
        return out

    async def chat(self, model, system, messages, tools, max_tokens=4096) -> AssistantTurn:
        body: dict[str, Any] = {"model": model, "messages": self.convert(system, messages)}
        body["max_completion_tokens" if self.kind == "openai" else "max_tokens"] = max_tokens
        if tools:
            body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                               "parameters": t.schema}} for t in tools]
        async with self.client() as c:
            r = await c.post(f"{self.base_url}/chat/completions", headers=self._headers(), json=body)
        self.raise_for(r, self.label)
        j = r.json()
        ch = (j.get("choices") or [{}])[0]
        msg = ch.get("message") or {}
        turn = AssistantTurn(text=msg.get("content") or "", stop_reason=ch.get("finish_reason", ""), usage=j.get("usage", {}))
        for t in msg.get("tool_calls") or []:
            try:
                args = json.loads(t["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_raw": t["function"].get("arguments")}
            turn.tool_calls.append({"id": t.get("id") or uuid.uuid4().hex, "name": t["function"]["name"], "args": args})
        return turn


class OpenAICompatibleProvider(OpenAIProvider):
    kind = "openai_compatible"
    label = "OpenAI-compatible API"
    default_base_url = ""
    needs_key = False
    suggested_models = []


# ------------------------------------------------------------------------------------------ Gemini
class GeminiProvider(Provider):
    kind = "gemini"
    label = "Google Gemini"
    default_base_url = "https://generativelanguage.googleapis.com/v1beta"
    suggested_models = ["gemini-2.5-pro", "gemini-2.5-flash"]

    def _headers(self) -> dict:
        return {"x-goog-api-key": self.api_key, "content-type": "application/json"}

    async def list_models(self) -> list[str]:
        async with self.client() as c:
            r = await c.get(f"{self.base_url}/models?pageSize=200", headers=self._headers())
            self.raise_for(r, "Gemini")
            return [m["name"].split("/", 1)[-1] for m in r.json().get("models", [])
                    if "generateContent" in m.get("supportedGenerationMethods", [])] or self.suggested_models

    @staticmethod
    def convert(messages: list[dict]) -> list[dict]:
        out = []
        for m in messages:
            if m["role"] == "user":
                parts = []
                for c in m["content"]:
                    if c["type"] == "text":
                        parts.append({"text": c["text"]})
                    elif c["type"] == "image":
                        parts.append({"inline_data": {"mime_type": c["media_type"], "data": c["data"]}})
                out.append({"role": "user", "parts": parts})
            elif m["role"] == "assistant":
                parts = [{"text": m["text"]}] if m.get("text") else []
                parts += [{"functionCall": {"name": t["name"], "args": t["args"]}} for t in m.get("tool_calls", [])]
                out.append({"role": "model", "parts": parts or [{"text": "…"}]})
            elif m["role"] == "tool":
                out.append({"role": "user", "parts": [
                    {"functionResponse": {"name": r["name"], "response": {"result": r["content"], "is_error": bool(r.get("is_error"))}}}
                    for r in m["results"]]})
        return out

    async def chat(self, model, system, messages, tools, max_tokens=4096) -> AssistantTurn:
        body: dict[str, Any] = {"contents": self.convert(messages), "generationConfig": {"maxOutputTokens": max_tokens}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = [{"functionDeclarations": [
                {"name": t.name, "description": t.description, "parameters": sanitize_schema(t.schema, True)} for t in tools]}]
        async with self.client() as c:
            r = await c.post(f"{self.base_url}/models/{model}:generateContent", headers=self._headers(), json=body)
        self.raise_for(r, "Gemini")
        j = r.json()
        cand = (j.get("candidates") or [{}])[0]
        turn = AssistantTurn(stop_reason=cand.get("finishReason", ""), usage=j.get("usageMetadata", {}))
        for p in (cand.get("content") or {}).get("parts", []):
            if "text" in p:
                turn.text += p["text"]
            elif "functionCall" in p:
                fc = p["functionCall"]
                turn.tool_calls.append({"id": uuid.uuid4().hex, "name": fc["name"], "args": fc.get("args") or {}})
        if not turn.text and not turn.tool_calls and cand.get("finishReason") not in (None, "STOP"):
            raise ProviderError(f"Gemini returned no content (finishReason={cand.get('finishReason')})")
        return turn


# ------------------------------------------------------------------------------------------ Ollama
class OllamaProvider(Provider):
    kind = "ollama"
    label = "Ollama (local models)"
    default_base_url = "http://localhost:11434"
    needs_key = False
    suggested_models = ["qwen3", "llama3.1", "mistral-nemo"]

    async def list_models(self) -> list[str]:
        async with self.client() as c:
            r = await c.get(f"{self.base_url}/api/tags")
            self.raise_for(r, "Ollama")
            return [m["name"] for m in r.json().get("models", [])]

    @staticmethod
    def convert(system: str, messages: list[dict]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}] if system else []
        for m in messages:
            if m["role"] == "user":
                msg: dict[str, Any] = {"role": "user", "content": _text_of(m["content"])}
                imgs = [c["data"] for c in m["content"] if c["type"] == "image"]
                if imgs:
                    msg["images"] = imgs
                out.append(msg)
            elif m["role"] == "assistant":
                msg = {"role": "assistant", "content": m.get("text") or ""}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [{"function": {"name": t["name"], "arguments": t["args"]}} for t in m["tool_calls"]]
                out.append(msg)
            elif m["role"] == "tool":
                for r in m["results"]:
                    out.append({"role": "tool", "content": r["content"], "tool_name": r["name"]})
        return out

    async def chat(self, model, system, messages, tools, max_tokens=4096) -> AssistantTurn:
        body: dict[str, Any] = {"model": model, "messages": self.convert(system, messages), "stream": False,
                                "options": {"num_predict": max_tokens}}
        if tools:
            body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                               "parameters": t.schema}} for t in tools]
        async with self.client() as c:
            r = await c.post(f"{self.base_url}/api/chat", json=body)
        self.raise_for(r, "Ollama")
        j = r.json()
        msg = j.get("message") or {}
        turn = AssistantTurn(text=msg.get("content") or "", stop_reason=j.get("done_reason", ""))
        for t in msg.get("tool_calls") or []:
            args = t["function"].get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            turn.tool_calls.append({"id": uuid.uuid4().hex, "name": t["function"]["name"], "args": args})
        return turn


REGISTRY: dict[str, type[Provider]] = {p.kind: p for p in
                                       (AnthropicProvider, OpenAIProvider, GeminiProvider, OllamaProvider, OpenAICompatibleProvider)}


def make(kind: str, api_key: str = "", base_url: str = "") -> Provider:
    cls = REGISTRY.get(kind)
    if not cls:
        raise ProviderError(f"Unknown provider kind '{kind}'")
    if cls.needs_key and not api_key:
        raise ProviderError(f"{cls.label} needs an API key")
    if kind == "openai_compatible" and not base_url:
        raise ProviderError("OpenAI-compatible provider needs a base URL (e.g. https://openrouter.ai/api/v1)")
    return cls(api_key, base_url)
