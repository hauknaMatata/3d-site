"""Client side of MCP: user-registered remote MCP servers (Streamable HTTP or SSE).

Only network transports are allowed (no stdio) because the backend is a shared web service — spawning
arbitrary local processes from user input would be remote code execution."""
from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from typing import Any

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client

from . import db, security

ALLOWED_SCHEMES = ("https://", "http://")


def _headers(row: dict) -> dict[str, str]:
    raw = security.decrypt(row.get("headers") or "")
    return json.loads(raw) if raw else {}


@asynccontextmanager
async def session_for(row: dict):
    url = row["url"]
    if not url.startswith(ALLOWED_SCHEMES):
        raise ValueError("MCP server URL must be http(s)")
    if row.get("transport") == "sse":
        async with sse_client(url, headers=_headers(row), timeout=20) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                yield s
    else:
        async with streamablehttp_client(url, headers=_headers(row), timeout=20) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                yield s


async def list_tools(row: dict) -> list[dict[str, Any]]:
    async with session_for(row) as s:
        res = await asyncio.wait_for(s.list_tools(), 30)
        return [{"name": t.name, "description": t.description or "", "input_schema": t.inputSchema or {"type": "object"},
                 "destructive": bool(getattr(t, "annotations", None) and getattr(t.annotations, "destructiveHint", False))}
                for t in res.tools]


async def call_tool(row: dict, name: str, args: dict) -> tuple[str, bool]:
    async with session_for(row) as s:
        res = await asyncio.wait_for(s.call_tool(name, args), 120)
        parts = []
        for c in res.content:
            if getattr(c, "type", "") == "text":
                parts.append(c.text)
            else:
                parts.append(f"[{getattr(c, 'type', 'content')}]")
        return "\n".join(parts)[:20000], bool(res.isError)


def tool_alias(server_name: str, tool: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_]", "_", server_name)[:20]
    return f"mcp_{s}__{re.sub(r'[^a-zA-Z0-9_-]', '_', tool)}"[:64]


def enabled_tools() -> list[dict[str, Any]]:
    """[{alias, server_row, tool}] for every enabled tool of every enabled server (cached tool lists)."""
    out = []
    for row in db.q("SELECT * FROM mcp_servers WHERE enabled=1"):
        if row["permission"] == "deny":
            continue
        disabled = set(json.loads(row["disabled_tools"] or "[]"))
        for t in json.loads(row["tools_cache"] or "[]"):
            if t["name"] in disabled:
                continue
            out.append({"alias": tool_alias(row["name"], t["name"]), "server": row, "tool": t})
    return out
