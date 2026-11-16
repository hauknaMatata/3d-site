"""Provider storage (encrypted keys) and model selection per task."""
from __future__ import annotations

import json
from typing import Any

from .. import db, security
from .providers.adapters import REGISTRY, make
from .providers.base import Provider, ProviderError

TASKS = {"modeling": "Modelowanie (agent narzędzi 3D)", "vision": "Analiza obrazów/załączników", "chat": "Rozmowa / pytania"}


def public_row(r: dict) -> dict[str, Any]:
    key = security.decrypt(r["api_key"]) if r.get("api_key") else ""
    return {"id": r["id"], "kind": r["kind"], "name": r["name"], "base_url": r["base_url"] or "",
            "has_key": bool(key), "key_preview": security.mask(key), "models": json.loads(r["models"] or "[]"),
            "default_model": r["default_model"] or "", "enabled": bool(r["enabled"]),
            "label": REGISTRY[r["kind"]].label if r["kind"] in REGISTRY else r["kind"]}


def instance(provider_id: str) -> tuple[Provider, dict]:
    r = db.q1("SELECT * FROM providers WHERE id=?", (provider_id,))
    if not r:
        raise ProviderError("Provider not found")
    if not r["enabled"]:
        raise ProviderError(f"Provider '{r['name']}' is disabled")
    return make(r["kind"], security.decrypt(r["api_key"] or ""), r["base_url"] or ""), r


def resolve(task: str, provider_id: str | None = None, model: str | None = None) -> tuple[Provider, str, dict] | None:
    """Pick (provider, model) for a task: explicit choice > task assignment > default provider."""
    if provider_id:
        p, r = instance(provider_id)
        return p, model or r["default_model"] or (json.loads(r["models"] or "[]") or [""])[0], r
    assign = (db.get_setting("assignments", {}) or {}).get(task)
    if assign and assign.get("provider_id"):
        try:
            p, r = instance(assign["provider_id"])
            return p, assign.get("model") or r["default_model"], r
        except ProviderError:
            pass
    default = db.get_setting("default_provider")
    if default:
        try:
            p, r = instance(default)
            return p, r["default_model"], r
        except ProviderError:
            pass
    return None
