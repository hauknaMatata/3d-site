"""Project runtime: the authoritative model state is the operation history; the engine state is
its replay. Gives undo/redo, parametric edits of any past operation and versioning for free."""
from __future__ import annotations

import asyncio
import copy
import json
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from . import config, db
from .engines.base import Engine, EngineContext, EngineError
from .engines.cad.engine import CadEngine
from .engines.game.engine import GameEngine
from .params import resolve

ENGINES: dict[str, type[Engine]] = {"cad": CadEngine, "game": GameEngine}


def empty_state() -> dict[str, Any]:
    return {"ops": [], "redo": [], "hidden": [], "locked": [], "rev": 0, "texture_res": 512, "params": {}}


class ProjectRuntime:
    def __init__(self, project: dict[str, Any]):
        self.id = project["id"]
        self.mode = project["mode"]
        self.name = project["name"]
        self.state = json.loads(project["state"]) if isinstance(project["state"], str) else project["state"]
        for k, v in empty_state().items():
            self.state.setdefault(k, v)
        self.engine: Engine = ENGINES[self.mode](EngineContext(self._attachment_path))
        self.lock = asyncio.Lock()
        self.tlock = threading.RLock()
        self.errors: dict[str, str] = {}
        self._rebuild()

    # ---------------------------------------------------------------- infra
    def _attachment_path(self, att_id: str) -> str:
        row = db.q1("SELECT path FROM attachments WHERE id=? AND project_id=?", (att_id, self.id))
        if not row:
            raise EngineError(f"Attachment '{att_id}' not found in this project.")
        return row["path"]

    def save(self) -> None:
        with db.tx() as c:
            c.execute("UPDATE projects SET state=?, updated=? WHERE id=?", (json.dumps(self.state), db.now(), self.id))

    def _rebuild(self) -> None:
        with self.tlock:
            self.engine.reset()
            if self.mode == "game":
                self.engine.texture_res = int(self.state.get("texture_res", 512))
            self.errors = {}
            for op in self.state["ops"]:
                try:
                    self.engine.apply(op["tool"], resolve(op["args"], self.state["params"]))
                except EngineError as e:
                    self.errors[op["id"]] = str(e)
            if self.mode == "game":
                self.state["texture_res"] = self.engine.texture_res

    # ---------------------------------------------------------------- mutations (call from a thread)
    def apply(self, tool: str, args: dict, label: str = "", source: str = "user", author: str | None = None) -> dict[str, Any]:
        with self.tlock:
            spec = self.engine.tools.get(tool)
            if spec is None:
                raise EngineError(f"Unknown tool '{tool}' for {self.mode.upper()} mode.")
            resolved = resolve(copy.deepcopy(args), self.state["params"])
            if not spec.mutating:
                return {"query": True, "result": self.engine.query(tool, resolved)}
            self._check_locked(tool, resolved)
            result = self.engine.apply(tool, resolved)
            op = {"id": db.new_id(), "tool": tool, "args": args, "label": label or tool.replace("_", " "),
                  "source": source, "author": author, "t": time.time()}
            self.state["ops"].append(op)
            self.state["redo"] = []
            if self.mode == "game":
                self.state["texture_res"] = self.engine.texture_res
            self._bump()
            return {"op": op, "result": result}

    def _check_locked(self, tool: str, args: dict) -> None:
        locked = set(self.state.get("locked", []))
        for key in ("target", "tool", "source"):
            v = args.get(key)
            if isinstance(v, str) and v in locked and tool not in ("set_visibility",):
                raise EngineError(f"Object '{v}' is locked. Unlock it first.")

    def undo(self) -> dict | None:
        with self.tlock:
            if not self.state["ops"]:
                return None
            op = self.state["ops"].pop()
            self.state["redo"].append(op)
            self._rebuild()
            self._bump()
            return op

    def redo(self) -> dict | None:
        with self.tlock:
            if not self.state["redo"]:
                return None
            op = self.state["redo"].pop()
            self.state["ops"].append(op)
            self._rebuild()
            self._bump()
            return op

    def update_op(self, op_id: str, args: dict, merge: bool = True) -> dict:
        """Parametric edit of a past operation; the whole history is replayed."""
        with self.tlock:
            for op in self.state["ops"]:
                if op["id"] == op_id:
                    old = copy.deepcopy(op["args"])
                    op["args"] = {**op["args"], **args} if merge else args
                    self._rebuild()
                    if op_id in self.errors:
                        err = self.errors[op_id]
                        op["args"] = old
                        self._rebuild()
                        raise EngineError(f"Edit rejected: {err}")
                    self.state["redo"] = []
                    self._bump()
                    return op
            raise EngineError(f"Operation {op_id} not found")

    def delete_op(self, op_id: str) -> dict:
        with self.tlock:
            ops = self.state["ops"]
            idx = next((i for i, o in enumerate(ops) if o["id"] == op_id), None)
            if idx is None:
                raise EngineError(f"Operation {op_id} not found")
            op = ops.pop(idx)
            self._rebuild()
            self.state["redo"] = []
            self._bump()
            return op

    def set_params(self, updates: dict[str, Any], remove: list[str] | None = None) -> dict:
        """Change user parameters; replays history and rolls back if any feature breaks."""
        with self.tlock:
            old = dict(self.state["params"])
            self.state["params"].update(updates)
            for k in remove or []:
                self.state["params"].pop(k, None)
            self._rebuild()
            if self.errors:
                bad = next(iter(self.errors.values()))
                self.state["params"] = old
                self._rebuild()
                raise EngineError(f"Parameter change rejected — it breaks the model: {bad}")
            self._bump()
            return dict(self.state["params"])

    def set_flags(self, name: str, hidden: bool | None = None, locked: bool | None = None) -> None:
        with self.tlock:
            for key, val in (("hidden", hidden), ("locked", locked)):
                if val is None:
                    continue
                s = set(self.state[key])
                (s.add if val else s.discard)(name)
                self.state[key] = sorted(s)
            self._bump()

    def restore(self, state: dict) -> None:
        with self.tlock:
            rev = self.state["rev"]
            self.state = {**empty_state(), **copy.deepcopy(state)}
            self.state["rev"] = rev
            self._rebuild()
            self._bump()

    def _bump(self) -> None:
        self.state["rev"] += 1
        self.save()

    # ---------------------------------------------------------------- output
    def render_glb(self) -> str:
        """Write the viewport GLB for the current revision; returns its URL path."""
        with self.tlock:
            rev = self.state["rev"]
            d = config.RENDER_DIR / self.id
            d.mkdir(parents=True, exist_ok=True)
            f = d / f"{rev}.glb"
            if not f.exists():
                scene = self.engine.to_trimesh_scene(hidden=set(self.state["hidden"]))
                if len(scene.geometry) == 0:
                    f.write_bytes(b"")
                else:
                    f.write_bytes(scene.export(file_type="glb", include_normals=True))
                # garbage-collect old revisions
                for old in d.glob("*.glb"):
                    try:
                        if int(old.stem) < rev - 5:
                            old.unlink()
                    except ValueError:
                        pass
            return f"/api/projects/{self.id}/model/{rev}.glb"

    def snapshot(self, with_model: bool = True) -> dict[str, Any]:
        with self.tlock:
            hidden, locked = set(self.state["hidden"]), set(self.state["locked"])
            objs = []
            for o in self.engine.objects():
                o["hidden"] = o["name"] in hidden
                o["locked"] = o["name"] in locked
                objs.append(o)
            ops = [{"id": o["id"], "tool": o["tool"], "label": o["label"], "args": o["args"], "source": o.get("source"), "author": o.get("author"),
                    "error": self.errors.get(o["id"])} for o in self.state["ops"]]
            empty = len(objs) == 0
            return {
                "project_id": self.id, "mode": self.mode, "rev": self.state["rev"], "objects": objs, "ops": ops,
                "can_undo": bool(self.state["ops"]), "can_redo": bool(self.state["redo"]),
                "model_url": (self.render_glb() if with_model and not empty else None),
                "units": "mm" if self.mode == "cad" else "m",
                "texture_res": self.state.get("texture_res"), "params": self.state.get("params", {}),
            }


_runtimes: dict[str, ProjectRuntime] = {}
_rt_lock = threading.Lock()


def get_runtime(pid: str) -> ProjectRuntime:
    with _rt_lock:
        rt = _runtimes.get(pid)
        if rt is None:
            row = db.q1("SELECT * FROM projects WHERE id=?", (pid,))
            if not row:
                raise KeyError(pid)
            rt = ProjectRuntime(row)
            _runtimes[pid] = rt
        return rt


def drop_runtime(pid: str) -> None:
    with _rt_lock:
        _runtimes.pop(pid, None)
    shutil.rmtree(config.RENDER_DIR / pid, ignore_errors=True)


def project_dir(pid: str) -> Path:
    d = config.FILES_DIR / pid
    d.mkdir(parents=True, exist_ok=True)
    return d
