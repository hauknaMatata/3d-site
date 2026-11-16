"""AI TEAM configuration: members (connection + model + role), collaboration mode, limits.
Role assignment adapts automatically to 1, 2, 3 or more enabled members; explicit roles win."""
from __future__ import annotations

import uuid
from typing import Any

from .. import db

ROLES: dict[str, dict[str, str]] = {
    "lead": {"label": "Lead Designer", "short": "LEAD",
             "duty": "analyse the brief, write the modeling plan, coordinate the team, weigh other agents' opinions and make final decisions"},
    "engineer": {"label": "3D Engineer", "short": "ENG",
                 "duty": "geometry: dimensions, topology, mesh errors, booleans, optimisation and export readiness"},
    "quality": {"label": "Texture & Quality Specialist", "short": "QA",
                "duty": "PBR materials, colours, textures, UV mapping, realism, detail and fidelity to the references"},
    "reviewer": {"label": "Reviewer", "short": "REV",
                 "duty": "independent critical review of plans and results"},
}
CORE_ROLES = ["lead", "engineer", "quality"]
STATUSES = ["idle", "thinking", "analyzing", "modeling", "reviewing", "fixing", "completed", "error"]
COLORS = ["#d97757", "#10a37f", "#4285f4", "#a970ff", "#e0a33a", "#3fb6b0"]

DEFAULTS: dict[str, Any] = {
    "members": [],
    "mode": "auto",                 # auto | review | manual
    "debate": True,
    "max_rounds": 2,                # QA → fix → re-check rounds
    "max_debate_rounds": 1,
    "build_max_turns": 30,          # tool-calling turns per build/fix agent
    "max_tokens": 600_000,          # whole team job
    "max_seconds": 1200,
    "max_cost_usd": None,           # needs per-member prices
    "auto_apply_max_severity": "major",  # minor | major | critical — above this, ask the user even in AUTO
    "retries": 3,
}
SEVERITY_ORDER = {"minor": 0, "major": 1, "critical": 2}


def load() -> dict[str, Any]:
    cfg = {**DEFAULTS, **(db.get_setting("team_config", {}) or {})}
    for i, m in enumerate(cfg["members"]):
        m.setdefault("id", uuid.uuid4().hex[:8])
        m.setdefault("enabled", True)
        m.setdefault("role", "auto")
        m.setdefault("order", i)
        m.setdefault("color", COLORS[i % len(COLORS)])
    return cfg


def save(cfg: dict[str, Any]) -> dict[str, Any]:
    clean = {k: cfg.get(k, v) for k, v in DEFAULTS.items()}
    if clean["mode"] not in ("auto", "review", "manual"):
        raise ValueError("mode must be auto|review|manual")
    if clean["auto_apply_max_severity"] not in SEVERITY_ORDER:
        raise ValueError("auto_apply_max_severity must be minor|major|critical")
    for k, lo, hi in (("max_rounds", 0, 5), ("max_debate_rounds", 0, 4), ("build_max_turns", 3, 80), ("retries", 0, 6)):
        clean[k] = max(lo, min(hi, int(clean[k])))
    members = []
    for i, m in enumerate(clean["members"]):
        if not m.get("provider_id"):
            raise ValueError("each member needs provider_id")
        if m.get("role", "auto") not in (*ROLES, "auto"):
            raise ValueError(f"unknown role {m.get('role')}")
        members.append({"id": m.get("id") or uuid.uuid4().hex[:8], "provider_id": m["provider_id"], "model": m.get("model") or "",
                        "role": m.get("role", "auto"), "enabled": bool(m.get("enabled", True)), "order": int(m.get("order", i)),
                        "label": (m.get("label") or "")[:40], "color": m.get("color") or COLORS[i % len(COLORS)],
                        "price_in": m.get("price_in"), "price_out": m.get("price_out")})
    clean["members"] = sorted(members, key=lambda x: x["order"])
    db.set_setting("team_config", clean)
    return load()


def assign_roles(members: list[dict], failed: set[str] | None = None) -> dict[str, list[str]]:
    """member_id -> roles. 1 member: all roles. 2: lead | engineer+quality. 3: one each. More: extras review.
    Explicit roles are honoured; missing core roles are filled by order; failed members are skipped."""
    failed = failed or set()
    act = [m for m in sorted(members, key=lambda m: m["order"]) if m["enabled"] and m["id"] not in failed]
    out: dict[str, list[str]] = {m["id"]: [] for m in act}
    if not act:
        return out
    taken: set[str] = set()
    for m in act:
        if m["role"] in CORE_ROLES and m["role"] not in taken:
            out[m["id"]].append(m["role"])
            taken.add(m["role"])
    free = [m for m in act if not out[m["id"]] and m["role"] in ("auto", "reviewer")]
    missing = [r for r in CORE_ROLES if r not in taken]
    if len(act) == 1:
        out[act[0]["id"]] = list(CORE_ROLES)
        return out
    for r in missing:
        if free:
            m = free.pop(0)
            out[m["id"]].append(r)
        else:  # fewer members than roles: stack the role on the member with the fewest roles (lead last)
            m = min(act, key=lambda x: (len(out[x["id"]]), "lead" in out[x["id"]]))
            out[m["id"]].append(r)
    for m in free:
        out[m["id"]].append("reviewer")
    return out


def member_for(role: str, roles: dict[str, list[str]], members: list[dict]) -> dict | None:
    by_id = {m["id"]: m for m in members}
    for mid, rs in roles.items():
        if role in rs:
            return by_id[mid]
    return None
