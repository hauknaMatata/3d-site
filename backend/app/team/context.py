"""Structured PROJECT CONTEXT shared by all agents. Each purpose gets only the sections it needs,
instead of the full chat history, to keep calls cheap and focused."""
from __future__ import annotations

import json
from typing import Any

from ..qa import compact

SECTIONS = {
    "plan": ["brief", "attachments", "params", "objects"],
    "critique": ["brief", "attachments", "params", "objects", "plan"],
    "debate": ["brief", "plan", "critiques"],
    "decide": ["brief", "plan", "critiques", "debate"],
    "build": ["brief", "attachments", "params", "objects", "final_plan", "ops"],
    "review": ["brief", "attachments", "params", "objects_full", "final_plan", "ops", "qa", "issues"],
    "fix_check": ["brief", "objects_full", "qa", "fixes"],
    "fix_decide": ["brief", "qa", "fixes", "fix_checks"],
    "summary": ["brief", "objects", "qa", "issues"],
}


def _cap(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + f"\n…(truncated {len(s) - n} chars)"


def build(rt, team: dict[str, Any], purpose: str, extra: dict[str, Any] | None = None) -> str:
    extra = extra or {}
    snap = rt.snapshot(False)
    out: list[str] = []
    for sec in SECTIONS[purpose]:
        if sec == "brief":
            out.append(f"## USER BRIEF\n{team['prompt']}\nMode: {rt.mode.upper()} ({'millimetres, Z up, B-Rep CAD' if rt.mode == 'cad' else 'metres, Y up, X forward, PBR meshes'})"
                       + (f"\nSelected object (edit scope): {team['selected']}" if team.get("selected") else ""))
        elif sec == "attachments" and team.get("attachments"):
            out.append("## ATTACHMENTS\n" + "\n".join(
                f"- attachment_id={a['id']} {a['filename']} {a.get('width')}×{a.get('height')}px palette={','.join(a.get('palette', [])[:5])}"
                for a in team["attachments"]))
        elif sec == "params":
            out.append(f"## USER PARAMETERS\n{json.dumps(snap['params'], ensure_ascii=False) or '{}'}")
        elif sec == "objects":
            objs = snap["objects"]
            roots = [o for o in objs if not o.get("parent")]
            lines = [f"- {o['name']} ({o['kind']}) dims={o.get('dims')} {o.get('material') or ''} {o.get('color') or ''}".rstrip()
                     for o in roots[:60]]
            out.append(f"## CURRENT MODEL ({len(objs)} objects, {len(roots)} top-level)\n" + ("\n".join(lines) or "(empty)"))
        elif sec == "objects_full":
            lines = [f"- {o['name']} kind={o['kind']} parent={o.get('parent')} dims={o.get('dims')} tris={o.get('triangles')} "
                     f"material={o.get('material')} color={o.get('color')} hidden={o['hidden']} locked={o['locked']}"
                     for o in snap["objects"][:150]]
            out.append(f"## CURRENT MODEL ({len(snap['objects'])} objects)\n" + "\n".join(lines))
        elif sec == "ops":
            ops = snap["ops"][-40:]
            out.append("## OPERATION HISTORY (latest)\n" + "\n".join(
                f"- {o['id']} {o['tool']} by {o.get('author') or o.get('source')}: {o['label']}" for o in ops) if ops else "## OPERATION HISTORY\n(none)")
        elif sec == "plan" and team.get("plan"):
            out.append("## LEAD DESIGNER PLAN\n" + _cap(json.dumps(team["plan"], ensure_ascii=False, indent=1), 6000))
        elif sec == "final_plan" and (team.get("final_plan") or team.get("plan")):
            out.append("## APPROVED PLAN\n" + _cap(json.dumps(team.get("final_plan") or team["plan"], ensure_ascii=False, indent=1), 6000))
        elif sec == "critiques" and team.get("critiques"):
            out.append("## INDEPENDENT CRITIQUES\n" + _cap(json.dumps(team["critiques"], ensure_ascii=False, indent=1), 8000))
        elif sec == "debate" and team.get("debate"):
            out.append("## DEBATE\n" + _cap(json.dumps(team["debate"], ensure_ascii=False, indent=1), 6000))
        elif sec == "qa" and team.get("qa"):
            out.append("## TECHNICAL QA (measured by code — the only source of measured facts)\n" + compact(team["qa"]))
        elif sec == "issues" and team.get("issues"):
            out.append("## ISSUES SO FAR\n" + "\n".join(
                f"- {i['id']} [{i['status']}] {i['severity']} {i['target']}: {i['problem'][:160]}" for i in team["issues"][-30:]))
        elif sec == "fixes" and extra.get("fixes"):
            out.append("## PROPOSED FIXES\n" + _cap(json.dumps(extra["fixes"], ensure_ascii=False, indent=1), 8000))
        elif sec == "fix_checks" and extra.get("fix_checks"):
            out.append("## CROSS-CHECKS BY OTHER AGENTS\n" + _cap(json.dumps(extra["fix_checks"], ensure_ascii=False, indent=1), 6000))
    return "\n\n".join(out)
