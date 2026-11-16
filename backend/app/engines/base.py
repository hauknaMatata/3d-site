"""Shared engine contract. Both pipelines (CAD + GAME) expose the same tool protocol so the
LLM agent, the MCP server, the offline planner and the REST API drive them identically."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


class EngineError(Exception):
    """A modeling operation failed (bad parameters, kernel failure…). Message is user-facing."""


@dataclass
class ToolSpec:
    name: str
    description: str
    params: dict[str, Any]  # JSON-schema "properties"
    required: list[str] = field(default_factory=list)
    mutating: bool = True  # False => query tool (measure…), not recorded in history
    dangerous: bool = False  # requires explicit user confirmation

    def schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": self.params, "required": self.required}


def vec3(desc: str = "[x, y, z]") -> dict:
    return {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "description": desc}


def color_prop(desc: str = "Color as #RRGGBB hex") -> dict:
    return {"type": "string", "description": desc}


@dataclass
class EngineContext:
    """What an engine may access outside its own state."""
    attachment_path: Callable[[str], str]  # attachment id -> local file path (raises if unknown)


class Engine:
    mode: str = ""
    tools: dict[str, ToolSpec] = {}

    def __init__(self, ctx: EngineContext):
        self.ctx = ctx

    def reset(self) -> None:
        raise NotImplementedError

    def apply(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def query(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def objects(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def object_names(self) -> list[str]:
        return [o["name"] for o in self.objects()]

    def to_trimesh_scene(self, hidden: set[str] | None = None, texture_res: int = 1024):
        raise NotImplementedError

    def summary(self) -> str:
        """Compact text description of the current scene for the LLM context."""
        lines = []
        for o in self.objects():
            dims = o.get("dims")
            d = f" size={[round(v, 2) for v in dims]}" if dims else ""
            extra = f" material={o['material']}" if o.get("material") else ""
            extra += f" color={o['color']}" if o.get("color") else ""
            parent = f" parent={o['parent']}" if o.get("parent") else ""
            lines.append(f"- {o['name']} ({o.get('kind', 'object')}){d}{extra}{parent}")
        return "\n".join(lines) or "(empty scene)"
