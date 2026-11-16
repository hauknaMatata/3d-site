"""GAME pipeline: textured, PBR-shaded mesh assets in a scene graph (Y-up, metres)."""
from __future__ import annotations

import math
import zlib
import re
from typing import Any

import numpy as np
import trimesh
from PIL import Image

from ..base import Engine, EngineError, ToolSpec, color_prop, vec3
from ..imaging import load_rgba
from . import meshes as M
from .textures import PRESETS, generate

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _f(a: dict, k: str, d: float | None = None, minv: float | None = 1e-5) -> float:
    v = a.get(k, d)
    if v is None:
        raise EngineError(f"Missing parameter '{k}'.")
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise EngineError(f"'{k}' must be a number") from None
    if minv is not None and v < minv:
        raise EngineError(f"'{k}' must be ≥ {minv}")
    return v


def _v(v, d=(0.0, 0.0, 0.0)) -> list[float]:
    if v is None:
        return list(d)
    if isinstance(v, (int, float)):
        return [float(v)] * 3
    if len(v) != 3:
        raise EngineError("vectors need 3 components")
    return [float(x) for x in v]


def euler_matrix(rot_deg) -> np.ndarray:
    rx, ry, rz = (math.radians(a) for a in rot_deg)
    return trimesh.transformations.euler_matrix(rx, ry, rz, "sxyz")


def trs(pos, rot, scl) -> np.ndarray:
    m = euler_matrix(rot)
    m[:3, :3] = m[:3, :3] * np.asarray(scl)[None, :]
    m[:3, 3] = pos
    return m


class GameEngine(Engine):
    mode = "game"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.reset()

    def reset(self) -> None:
        self.parts: dict[str, dict[str, Any]] = {}
        self.texture_res = 512

    # ------------------------------------------------------------ helpers
    def _part(self, name: str) -> dict:
        if name not in self.parts:
            raise EngineError(f"Object '{name}' does not exist. Existing: {', '.join(list(self.parts)[:40]) or 'none'}")
        return self.parts[name]

    def _uniq(self, name: str | None, base: str) -> str:
        n = (name or base).strip() or base
        if n not in self.parts:
            return n
        i = 2
        while f"{n}_{i}" in self.parts:
            i += 1
        return f"{n}_{i}"

    def _material(self, a: dict, fallback: dict | None = None) -> dict:
        m = a.get("material")
        if isinstance(m, str):
            m = {"preset": m}
        m = dict(m or fallback or {"preset": "plastic", "color": "#9aa3ad"})
        if m.get("preset") not in PRESETS:
            raise EngineError(f"Unknown material preset '{m.get('preset')}'. Available: {', '.join(PRESETS)}")
        if m.get("color") and not HEX.match(m["color"]):
            raise EngineError("material.color must be #RRGGBB")
        if a.get("color") and HEX.match(a["color"]):
            m["color"] = a["color"]
        return m

    def _add(self, name: str, mesh: trimesh.Trimesh | None, a: dict, kind: str, material: dict | None = None,
             parent: str | None = None) -> str:
        parent = parent if parent is not None else a.get("parent")
        if parent and parent not in self.parts:
            raise EngineError(f"Parent '{parent}' does not exist")
        scl = a.get("scale", 1)
        self.parts[name] = {
            "mesh": mesh, "kind": kind, "parent": parent or None,
            "material": material if material is not None else (self._material(a) if mesh is not None else None),
            "position": _v(a.get("position")), "rotation": _v(a.get("rotation")),
            "scale": _v(scl, (1, 1, 1)), "meta": {},
        }
        return name

    def world_matrix(self, name: str) -> np.ndarray:
        p = self.parts[name]
        m = trs(p["position"], p["rotation"], p["scale"])
        return self.world_matrix(p["parent"]) @ m if p["parent"] else m

    def _children(self, name: str) -> list[str]:
        out = []
        for n, p in self.parts.items():
            if p["parent"] == name:
                out.append(n)
                out.extend(self._children(n))
        return out

    # ------------------------------------------------------------ apply
    def apply(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        fn = getattr(self, f"op_{tool}", None)
        if fn is None or tool not in self.tools or not self.tools[tool].mutating:
            raise EngineError(f"Unknown GAME tool '{tool}'.")
        try:
            return fn(args) or {}
        except EngineError:
            raise
        except Exception as e:
            raise EngineError(f"{tool} failed: {e}") from e

    def op_set_texture_resolution(self, a):
        r = int(a.get("resolution", 1024))
        if r not in (256, 512, 1024, 2048):
            raise EngineError("resolution must be 256, 512, 1024 or 2048")
        self.texture_res = r
        return {"texture_resolution": r}

    def op_add_group(self, a):
        n = self._uniq(a.get("name"), "Group")
        self._add(n, None, a, "group")
        return {"object": n}

    def op_add_primitive(self, a):
        s = a.get("shape", "box")
        seg = int(a.get("segments", 32))
        if s == "box":
            w, h, d = _v(a.get("size"), (1, 1, 1))
            mesh = M.rounded_box(w, h, d, float(a.get("bevel", 0)), int(a.get("bevel_segments", 3)))
        elif s == "rounded_box":
            w, h, d = _v(a.get("size"), (1, 1, 1))
            mesh = M.rounded_box(w, h, d, _f(a, "radius", 0.05), int(a.get("bevel_segments", 4)))
        elif s == "cylinder":
            mesh = M.cylinder(_f(a, "radius", 0.5), _f(a, "height", 1), seg, a.get("radius_top"))
        elif s == "cone":
            mesh = M.cone(_f(a, "radius", 0.5), _f(a, "height", 1), seg)
        elif s == "sphere":
            mesh = M.sphere(_f(a, "radius", 0.5), seg, max(8, seg // 2))
        elif s == "capsule":
            mesh = M.capsule(_f(a, "radius", 0.25), _f(a, "length", 1, 0), max(12, seg))
        elif s == "torus":
            mesh = M.torus(_f(a, "radius", 0.5), _f(a, "tube_radius", 0.1), max(12, seg), max(8, seg // 2))
        elif s == "wedge":
            w, h, d = _v(a.get("size"), (1, 1, 1))
            mesh = M.wedge(w, h, d)
        elif s == "plane":
            w, _, d = _v(a.get("size"), (1, 0, 1))
            mesh = M.plane(w, d, int(a.get("subdivisions", 1)))
        else:
            raise EngineError(f"Unknown shape '{s}'")
        n = self._uniq(a.get("name"), s.capitalize())
        self._add(n, mesh, a, s)
        return {"object": n, "triangles": len(mesh.faces)}

    def op_add_lathe(self, a):
        prof = a.get("profile") or []
        if len(prof) < 2:
            raise EngineError("profile needs ≥2 [radius, y] points")
        mesh = M.lathe([(max(0.0, float(r)), float(y)) for r, y in prof], int(a.get("segments", 48)),
                       float(a.get("angle", 360)))
        n = self._uniq(a.get("name"), "Lathe")
        self._add(n, mesh, a, "lathe")
        return {"object": n, "triangles": len(mesh.faces)}

    def op_add_loft(self, a):
        secs = a.get("sections") or []
        if len(secs) < 2:
            raise EngineError("loft needs ≥2 sections")
        try:
            mesh = M.loft(secs, int(a.get("segments", 48)), True, int(a.get("smoothness", 4)))
        except ValueError as e:
            raise EngineError(str(e)) from e
        n = self._uniq(a.get("name"), "Loft")
        self._add(n, mesh, a, "loft")
        return {"object": n, "triangles": len(mesh.faces)}

    def op_add_extrusion(self, a):
        pts = a.get("points") or []
        if len(pts) < 3:
            raise EngineError("extrusion needs ≥3 [x, y] points")
        mesh = M.extrude_polygon([(float(p[0]), float(p[1])) for p in pts], _f(a, "depth", 0.1))
        n = self._uniq(a.get("name"), "Extrusion")
        self._add(n, mesh, a, "extrusion")
        return {"object": n, "triangles": len(mesh.faces)}

    def op_add_tube(self, a):
        path = a.get("path") or []
        if len(path) < 2:
            raise EngineError("tube path needs ≥2 points")
        mesh = M.tube(path, _f(a, "radius", 0.02), int(a.get("segments", 12)), bool(a.get("closed", False)))
        n = self._uniq(a.get("name"), "Tube")
        self._add(n, mesh, a, "tube")
        return {"object": n, "triangles": len(mesh.faces)}

    def op_add_wheel(self, a):
        """Composite: tyre with tread, multi-spoke rim, brake disc + caliper. Axis = Z (car's side)."""
        r, w = _f(a, "radius", 0.34), _f(a, "width", 0.25)
        spokes = int(a.get("spokes", 5))
        root = self._uniq(a.get("name"), "Wheel")
        self._add(root, None, a, "group")
        side = 1 if float(a.get("side", 1)) >= 0 else -1
        rim_r = r * float(a.get("rim_ratio", 0.68))
        # tyre profile (radius, y) — rounded shoulders, revolved around Y then rotated so axis = Z
        prof = []
        for t in np.linspace(-math.pi / 2, math.pi / 2, 9):
            prof.append((r - (w * 0.18) * (1 - math.cos(t)), math.sin(t) * w / 2))
        tyre_prof = [(rim_r * 0.98, -w * 0.45)] + prof + [(rim_r * 0.98, w * 0.45)]
        tyre = M.lathe(tyre_prof, 64, cap=False)
        tyre = M.uv_project(tyre, "cylindrical", 1.0)
        rot = [90 * side, 0, 0]
        self._add(f"{root}_Tire", tyre, {"rotation": rot}, "tire", self._material(
            {"material": a.get("tire_material") or {"preset": "tire"}}), parent=root)
        rim_mat = self._material({"material": a.get("rim_material") or {"preset": "metal_brushed", "color": "#1a1a1c"}})
        barrel = M.lathe([(rim_r, -w * 0.42), (rim_r * 0.97, -w * 0.3), (rim_r * 0.9, w * 0.3), (rim_r, w * 0.42)], 48, cap=False)
        self._add(f"{root}_RimBarrel", barrel, {"rotation": rot}, "rim", rim_mat, parent=root)
        lip = M.torus(rim_r, w * 0.03, 48, 8)
        self._add(f"{root}_RimLip", lip, {"rotation": rot, "position": [0, 0, side * w * 0.42]}, "rim", rim_mat, parent=root)
        hub = M.cylinder(rim_r * 0.22, w * 0.18, 24)
        self._add(f"{root}_Hub", hub, {"rotation": rot, "position": [0, 0, side * w * 0.3]}, "rim", rim_mat, parent=root)
        for i in range(spokes):
            ang = 360 / spokes * i
            sp = M.rounded_box(rim_r * 0.82, rim_r * 0.16, w * 0.1, rim_r * 0.04, 2)
            sp.apply_translation([rim_r * 0.5, 0, 0])
            self._add(f"{root}_Spoke{i + 1}", sp, {"rotation": [0, 0, ang], "position": [0, 0, side * w * 0.33]},
                      "spoke", rim_mat, parent=root)
        disc = M.cylinder(rim_r * 0.78, w * 0.06, 40)
        self._add(f"{root}_BrakeDisc", disc, {"rotation": rot, "position": [0, 0, side * w * 0.05]}, "brake",
                  self._material({"material": {"preset": "metal_brushed", "color": "#8d9298"}}), parent=root)
        cal = M.rounded_box(rim_r * 0.35, rim_r * 0.5, w * 0.22, 0.01, 2)
        self._add(f"{root}_Caliper", cal, {"position": [rim_r * 0.55, rim_r * 0.25, side * w * 0.08]}, "brake",
                  self._material({"material": {"preset": "car_paint", "color": a.get("caliper_color", "#d01818")}}),
                  parent=root)
        return {"object": root, "children": len(self._children(root))}

    def op_add_humanoid(self, a):
        """Composite stylised character built from organic primitives with per-part colours."""
        H = _f(a, "height", 1.8)
        c = {"skin": "#e0ac8a", "hair": "#3b2416", "shirt": "#2d5fa8", "pants": "#2b2f3a", "shoes": "#1b1b1b",
             "eyes": "#2a3b4c", **(a.get("colors") or {})}
        for k, v in c.items():
            if not HEX.match(v):
                raise EngineError(f"colors.{k} must be #RRGGBB")
        build = float(a.get("build", 1.0))
        u = H / 8.0  # 8-head proportions
        root = self._uniq(a.get("name"), "Character")
        self._add(root, None, a, "group")

        def part(n, mesh, pos, mat, rot=(0, 0, 0), scl=(1, 1, 1)):
            self._add(f"{root}_{n}", mesh, {"position": pos, "rotation": list(rot), "scale": list(scl)}, "body",
                      self._material({"material": mat}), parent=root)

        skin = {"preset": "skin", "color": c["skin"]}
        cloth = {"preset": a.get("shirt_material", "fabric"), "color": c["shirt"]}
        pants = {"preset": "fabric", "color": c["pants"]}
        part("Head", M.sphere(u * 0.5, 40, 24), [0, H - u * 0.55, 0], skin, scl=(0.92, 1.05, 1.0))
        part("Hair", M.sphere(u * 0.53, 40, 24), [0, H - u * 0.45, -u * 0.04], {"preset": "fabric", "color": c["hair"]},
             scl=(0.95, 0.85, 1.02))
        for sx in (-1, 1):
            part(f"Eye{'L' if sx < 0 else 'R'}", M.sphere(u * 0.07, 16, 10), [sx * u * 0.17, H - u * 0.55, u * 0.43],
                 {"preset": "plastic", "color": c["eyes"], "roughness": 0.1})
        part("Neck", M.cylinder(u * 0.18, u * 0.35, 20), [0, H - u * 1.15, 0], skin)
        torso = M.loft([
            {"x": 0, "width": u * 0.95 * build, "height": u * 1.35 * build, "exp_top": 2.6, "exp_bottom": 2.6},
            {"x": u * 1.1, "width": u * 0.8 * build, "height": u * 1.1 * build, "exp_top": 2.4, "exp_bottom": 2.4},
            {"x": u * 2.1, "width": u * 0.95 * build, "height": u * 1.25 * build, "exp_top": 2.4, "exp_bottom": 2.4},
            {"x": u * 2.75, "width": u * 0.85, "height": u * 1.2, "exp_top": 2.2, "exp_bottom": 2.2},
        ], 40)
        # loft is along X; rotate so it stands along Y (shoulders on top)
        part("Torso", torso, [0, H - u * 4.1, 0], cloth, rot=(0, 0, 90))
        for sx, side in ((-1, "L"), (1, "R")):
            part(f"UpperArm{side}", M.capsule(u * 0.2 * build, u * 1.15, 20), [sx * u * 1.05, H - u * 2.1, 0], cloth,
                 rot=(0, 0, sx * 8))
            part(f"Forearm{side}", M.capsule(u * 0.16 * build, u * 1.05, 20), [sx * u * 1.18, H - u * 3.4, u * 0.05], skin,
                 rot=(0, 0, sx * 4))
            part(f"Hand{side}", M.sphere(u * 0.2, 18, 12), [sx * u * 1.22, H - u * 4.15, u * 0.06], skin, scl=(0.8, 1.2, 0.6))
            part(f"Thigh{side}", M.capsule(u * 0.27 * build, u * 1.5, 20), [sx * u * 0.42, H - u * 4.95, 0], pants)
            part(f"Shin{side}", M.capsule(u * 0.2 * build, u * 1.55, 20), [sx * u * 0.42, H - u * 6.65, 0], pants)
            part(f"Shoe{side}", M.rounded_box(u * 0.42, u * 0.3, u * 0.85, u * 0.12, 3), [sx * u * 0.42, u * 0.15, u * 0.15],
                 {"preset": "leather", "color": c["shoes"]})
        part("Belt", M.torus(u * 0.47 * build, u * 0.06, 32, 8), [0, H - u * 4.05, 0],
             {"preset": "leather", "color": "#2a1f19"}, scl=(1.0, 1.0, 0.72))
        return {"object": root, "children": len(self._children(root))}

    def op_add_tree(self, a):
        H = _f(a, "height", 5.0)
        kind = a.get("kind", "broadleaf")
        root = self._uniq(a.get("name"), "Tree")
        self._add(root, None, a, "group")
        rng = np.random.default_rng(int(a.get("seed", 3)))
        trunk = M.lathe([(H * 0.06, 0), (H * 0.045, H * 0.3), (H * 0.03, H * 0.62), (H * 0.012, H * 0.8)], 20)
        self._add(f"{root}_Trunk", trunk, {}, "trunk", self._material({"material": {"preset": "bark"}}), parent=root)
        leaf = self._material({"material": {"preset": "foliage", "color": a.get("leaf_color", "#3f7d2c")}})
        if kind == "conifer":
            for i in range(5):
                y = H * (0.25 + i * 0.14)
                cn = M.cone(H * (0.3 - i * 0.045), H * 0.3, 24)
                self._add(f"{root}_Crown{i + 1}", cn, {"position": [0, y + H * 0.15, 0]}, "foliage", leaf, parent=root)
        else:
            for i in range(7):
                rr = H * rng.uniform(0.14, 0.22)
                pos = [rng.uniform(-1, 1) * H * 0.14, H * rng.uniform(0.62, 0.86), rng.uniform(-1, 1) * H * 0.14]
                bl = M.sphere(rr, 24, 14)
                bl.vertices += (np.random.default_rng(i).random(bl.vertices.shape) - 0.5) * rr * 0.12
                self._add(f"{root}_Foliage{i + 1}", bl, {"position": pos}, "foliage", leaf, parent=root)
        return {"object": root, "children": len(self._children(root))}

    def op_add_terrain(self, a):
        w = _f(a, "size", 30)
        amp = float(a.get("height", 1.5))
        seed = int(a.get("seed", 1))
        rng = np.random.default_rng(seed)
        ph = rng.random(6) * 10

        def hf(X, Z):
            y = sum(np.sin(X * f / w * 6 + ph[i]) * np.cos(Z * f / w * 5 + ph[i + 3]) / f
                    for i, f in enumerate((1.0, 2.3, 4.7)))
            return y * amp

        mesh = M.plane(w, w, int(a.get("subdivisions", 64)), hf)
        n = self._uniq(a.get("name"), "Terrain")
        self._add(n, mesh, {**a, "material": a.get("material") or {"preset": "grass"}}, "terrain")
        return {"object": n, "triangles": len(mesh.faces)}

    def op_set_material(self, a):
        p = self._part(a.get("target", ""))
        targets = [a["target"]] + (self._children(a["target"]) if a.get("include_children", True) else [])
        filt = a.get("only_kind")
        n = 0
        for t in targets:
            q = self.parts[t]
            if q["mesh"] is None or (filt and q["kind"] != filt):
                continue
            base = dict(q["material"] or {})
            upd = a.get("material") or {}
            if isinstance(upd, str):
                upd = {"preset": upd}
            for k in ("color", "roughness", "metallic", "emissive", "opacity", "image"):
                if a.get(k) is not None:
                    upd[k] = a[k]
            if a.get("image"):
                self.ctx.attachment_path(a["image"])  # validate
            if upd.get("preset") and upd["preset"] != base.get("preset"):
                base = {"preset": upd["preset"]}  # new preset: drop previous colour/overrides
            base.update({k: v for k, v in upd.items() if v is not None})
            q["material"] = self._material({"material": base})
            n += 1
        if n == 0 and p["mesh"] is None:
            raise EngineError("No meshes affected (check only_kind).")
        return {"updated": n}

    def op_transform(self, a):
        p = self._part(a.get("target", ""))
        if a.get("position") is not None:
            p["position"] = _v(a["position"])
        if a.get("translate") is not None:
            p["position"] = list(np.add(p["position"], _v(a["translate"])))
        if a.get("rotation") is not None:
            p["rotation"] = _v(a["rotation"])
        if a.get("scale") is not None:
            p["scale"] = _v(a["scale"], (1, 1, 1))
        if a.get("parent") is not None:
            if a["parent"] and (a["parent"] == a["target"] or a["parent"] in self._children(a["target"])):
                raise EngineError("Cannot parent an object to itself or its child.")
            p["parent"] = a["parent"] or None
        return {"object": a["target"]}

    def _clone(self, src: str, new: str, parent: str | None, mirror_axis: int | None = None) -> None:
        p = self.parts[src]
        mesh = p["mesh"].copy() if p["mesh"] is not None else None
        q = {**p, "mesh": mesh, "meta": dict(p["meta"]), "position": list(p["position"]),
             "rotation": list(p["rotation"]), "scale": list(p["scale"]), "parent": parent}
        if mirror_axis is not None:
            q["position"][mirror_axis] *= -1
            # mirroring a rotation: negate the two other axes' rotations
            q["rotation"] = [(-r if i != mirror_axis else r) for i, r in enumerate(q["rotation"])]
        self.parts[new] = q
        for ch in [n for n, c in list(self.parts.items()) if c["parent"] == src]:
            self._clone(ch, new + ch[len(src):] if ch.startswith(src) else self._uniq(None, ch), new, mirror_axis)

    def op_duplicate(self, a):
        src = a.get("target", "")
        p = self._part(src)
        n = self._uniq(a.get("name"), src + "_copy")
        self._clone(src, n, p["parent"])
        if a.get("position") is not None:
            self.parts[n]["position"] = _v(a["position"])
        if a.get("translate") is not None:
            self.parts[n]["position"] = list(np.add(self.parts[n]["position"], _v(a["translate"])))
        return {"object": n}

    def op_mirror(self, a):
        src = a.get("target", "")
        p = self._part(src)
        axis = "xyz".index((a.get("axis") or "x").lower())
        n = self._uniq(a.get("name"), src + "_mirror")
        self._clone(src, n, p["parent"], axis)
        # geometry itself must be mirrored for asymmetric meshes (only top-level meshes are flipped in place)
        if self.parts[n]["mesh"] is not None and a.get("mirror_geometry", False):
            m = self.parts[n]["mesh"]
            m.vertices[:, axis] *= -1
            m.faces = m.faces[:, ::-1]
        return {"object": n}

    def op_array(self, a):
        src = a.get("target", "")
        p = self._part(src)
        cnt = int(a.get("count", 3))
        if not 2 <= cnt <= 200:
            raise EngineError("count must be 2..200")
        off = _v(a.get("offset"), (1, 0, 0))
        rot = _v(a.get("rotation_step"), (0, 0, 0))
        made = []
        for i in range(1, cnt):
            n = self._uniq(None, f"{src}_{i + 1}")
            self._clone(src, n, p["parent"])
            self.parts[n]["position"] = list(np.add(p["position"], np.multiply(off, i)))
            self.parts[n]["rotation"] = list(np.add(p["rotation"], np.multiply(rot, i)))
            made.append(n)
        return {"objects": made}

    def op_delete(self, a):
        t = a.get("target", "")
        self._part(t)
        for n in [t] + self._children(t):
            self.parts.pop(n, None)
        return {"deleted": t}

    def op_rename(self, a):
        t = a.get("target", "")
        self._part(t)
        new = (a.get("new_name") or "").strip()
        if not new or new in self.parts:
            raise EngineError("new_name must be unique and non-empty")
        self.parts = {(new if k == t else k): v for k, v in self.parts.items()}
        for v in self.parts.values():
            if v["parent"] == t:
                v["parent"] = new
        return {"object": new}

    def _world_mesh(self, name: str) -> trimesh.Trimesh:
        p = self.parts[name]
        m = p["mesh"].copy()
        m.apply_transform(self.world_matrix(name))
        return m

    def op_boolean(self, a):
        t, tool = a.get("target", ""), a.get("tool", "")
        pt, pl = self._part(t), self._part(tool)
        if pt["mesh"] is None or pl["mesh"] is None:
            raise EngineError("boolean works on mesh objects, not groups")
        A, B = M.watertight_copy(self._world_mesh(t)), M.watertight_copy(self._world_mesh(tool))
        op = a.get("operation", "difference")
        try:
            res = {"difference": trimesh.boolean.difference, "union": trimesh.boolean.union,
                   "intersection": trimesh.boolean.intersection}[op]([A, B], engine="manifold")
        except KeyError:
            raise EngineError("operation must be union|difference|intersection") from None
        except Exception as e:
            raise EngineError(f"Mesh boolean failed (meshes must be closed/watertight): {e}") from e
        res.apply_transform(np.linalg.inv(self.world_matrix(t)))
        res = M.uv_project(res, "box")
        pt["mesh"] = res
        if not a.get("keep_tool", False):
            self.op_delete({"target": tool})
        return {"object": t, "triangles": len(res.faces)}

    def op_smooth(self, a):
        p = self._part(a.get("target", ""))
        if p["mesh"] is None:
            raise EngineError("Target is a group")
        m = p["mesh"]
        it = int(a.get("iterations", 1))
        for _ in range(min(it, 3)):
            v, f = trimesh.remesh.subdivide_loop(m.vertices, m.faces) if len(m.faces) < 60000 else (m.vertices, m.faces)
            m = trimesh.Trimesh(v, f, process=False)
        p["mesh"] = M.uv_project(m, a.get("uv", "box"))
        return {"object": a["target"], "triangles": len(p["mesh"].faces)}

    def op_uv_unwrap(self, a):
        p = self._part(a.get("target", ""))
        if p["mesh"] is None:
            raise EngineError("Target is a group")
        try:
            p["mesh"] = M.uv_project(p["mesh"], a.get("method", "box"), float(a.get("scale", 1)))
        except ValueError as e:
            raise EngineError(str(e)) from e
        return {"object": a["target"]}

    def _targets(self, t: str | None) -> list[str]:
        if not t or t == "*":
            return [n for n, p in self.parts.items() if p["mesh"] is not None]
        self._part(t)
        return [n for n in [t] + self._children(t) if self.parts[n]["mesh"] is not None]

    def op_decimate(self, a):
        import fast_simplification

        ratio = float(a.get("ratio", 0.5))
        if not 0.01 <= ratio < 1:
            raise EngineError("ratio must be in [0.01, 1) — fraction of triangles to REMOVE is 1-ratio")
        before = after = 0
        for n in self._targets(a.get("target")):
            m = self.parts[n]["mesh"]
            before += len(m.faces)
            if len(m.faces) < 64:
                after += len(m.faces)
                continue
            mm = m.copy()
            mm.merge_vertices(merge_tex=True, merge_norm=True)
            v, f = fast_simplification.simplify(mm.vertices.astype(np.float32), mm.faces.astype(np.int32), 1 - ratio)
            nm = M.uv_project(trimesh.Trimesh(v, f, process=False), "box")
            self.parts[n]["mesh"] = nm
            after += len(nm.faces)
        return {"triangles_before": before, "triangles_after": after}

    def op_generate_lods(self, a):
        import fast_simplification

        ratios = [float(r) for r in (a.get("ratios") or [0.5, 0.25])]
        count = 0
        for n in self._targets(a.get("target")):
            m = self.parts[n]["mesh"]
            lods = []
            for r in ratios:
                mm = m.copy()
                mm.merge_vertices(merge_tex=True, merge_norm=True)
                if len(mm.faces) < 64:
                    lods.append(None)
                    continue
                v, f = fast_simplification.simplify(mm.vertices.astype(np.float32), mm.faces.astype(np.int32), 1 - r)
                lods.append(M.uv_project(trimesh.Trimesh(v, f, process=False), "box"))
            self.parts[n]["meta"]["lods"] = lods
            count += 1
        return {"objects_with_lods": count, "levels": len(ratios)}

    def op_optimize_for(self, a):
        platform = (a.get("platform") or "").lower()
        budgets = {"roblox": 10000, "unity": 50000, "unreal": 80000, "web": 20000, "mobile": 5000}
        if platform not in budgets:
            raise EngineError(f"platform must be one of {', '.join(budgets)}")
        budget = budgets[platform]
        total_before = sum(len(p["mesh"].faces) for p in self.parts.values() if p["mesh"] is not None)
        changed = 0
        # per-object budget: Roblox MeshParts are limited to ~20k triangles each; we keep a safety margin
        for n in self._targets(None):
            f = len(self.parts[n]["mesh"].faces)
            if f > budget:
                self.op_decimate({"target": n, "ratio": max(0.02, budget / f)})
                changed += 1
        if platform in ("unity", "unreal"):
            self.op_generate_lods({"ratios": [0.5, 0.25]})
        if platform in ("web", "mobile"):
            self.texture_res = 512 if platform == "web" else 256
        total_after = sum(len(p["mesh"].faces) for p in self.parts.values() if p["mesh"] is not None)
        return {"platform": platform, "per_object_budget": budget, "objects_decimated": changed,
                "triangles_before": total_before, "triangles_after": total_after,
                "notes": {"roblox": "Export FBX or OBJ; 1 stud ≈ 0.28 m — set export scale ≈ 3.57 for real-world size.",
                          "unity": "Export FBX or glTF; LOD nodes are named *_LOD0/1/2.",
                          "unreal": "Export FBX; LOD nodes are named *_LOD0/1/2; units are cm (scale 100).",
                          "web": "Export GLB.", "mobile": "Export GLB/FBX."}[platform]}

    def op_apply_decal(self, a):
        """Project an uploaded image onto the surface of an object (fit to surface) as a decal mesh."""
        t = a.get("target", "")
        p = self._part(t)
        if p["mesh"] is None:
            raise EngineError("Target must be a mesh (pick a body part, not a group)")
        att = a.get("attachment_id") or ""
        self.ctx.attachment_path(att)
        side = (a.get("side") or "+z").lower()
        if side not in ("+x", "-x", "+y", "-y", "+z", "-z"):
            raise EngineError("side must be ±x/±y/±z (object local axes)")
        axis = "xyz".index(side[1])
        sign = 1 if side[0] == "+" else -1
        m = p["mesh"]
        bmin, bmax = m.bounds
        uax = [i for i in range(3) if i != axis]
        if axis == 1:
            uax = [0, 2]
        elif axis == 2:
            uax = [0, 1]
        else:
            uax = [2, 1]
        cu, cv = (a.get("center") or [0.5, 0.5])[:2]
        cu = bmin[uax[0]] + (bmax[uax[0]] - bmin[uax[0]]) * float(cu)
        cv = bmin[uax[1]] + (bmax[uax[1]] - bmin[uax[1]]) * float(cv)
        img = load_rgba(self.ctx.attachment_path(att), 1024)
        wd = _f(a, "width", 0.5)
        ht = float(a.get("height") or wd * img.height / img.width)
        ang = math.radians(float(a.get("rotation", 0)))
        tri = m.vertices[m.faces]
        cen = tri.mean(1)
        fn = m.face_normals
        d_u, d_v = cen[:, uax[0]] - cu, cen[:, uax[1]] - cv
        ru = d_u * math.cos(ang) + d_v * math.sin(ang)
        rv = -d_u * math.sin(ang) + d_v * math.cos(ang)
        sel = (fn[:, axis] * sign > 0.25) & (np.abs(ru) < wd / 2 + 0.02) & (np.abs(rv) < ht / 2 + 0.02)
        # keep only the outermost layer along the projection axis
        if sel.any():
            lim = np.percentile(cen[sel, axis] * sign, 40)
            sel &= cen[:, axis] * sign >= lim - (bmax[axis] - bmin[axis]) * 0.25
        if not sel.any():
            raise EngineError("No surface found under the decal area — adjust side/center/width.")
        sub = m.submesh([np.where(sel)[0]], append=True)
        vn = sub.vertex_normals
        v = sub.vertices + vn * float(a.get("offset", 0.002))
        du, dv = v[:, uax[0]] - cu, v[:, uax[1]] - cv
        uu = (du * math.cos(ang) + dv * math.sin(ang)) / wd + 0.5
        vv = (-du * math.sin(ang) + dv * math.cos(ang)) / ht + 0.5
        if sign < 0 and axis != 1:
            uu = 1 - uu
        dm = trimesh.Trimesh(v, sub.faces, vertex_normals=vn, process=False)
        dm.visual = trimesh.visual.TextureVisuals(uv=np.stack([uu, vv], 1))
        n = self._uniq(a.get("name"), f"{t}_Decal")
        self._add(n, dm, {"parent": t}, "decal", {"preset": "plastic", "color": "#ffffff", "decal": att,
                                                     "roughness": float(a.get("roughness", 0.35))})
        return {"object": n, "triangles": len(dm.faces)}

    def op_import_mesh(self, a):
        path = self.ctx.attachment_path(a.get("attachment_id", ""))
        try:
            loaded = trimesh.load(path, force="scene")
        except Exception as e:
            raise EngineError(f"Could not import mesh: {e}") from e
        root = self._uniq(a.get("name"), "Imported")
        self._add(root, None, a, "group")
        s = float(a.get("import_scale", 1.0))
        for i, (gname, g) in enumerate(loaded.geometry.items()):
            if not isinstance(g, trimesh.Trimesh):
                continue
            g = g.copy()
            g.apply_scale(s)
            if not hasattr(g.visual, "uv") or g.visual.uv is None:
                g = M.uv_project(g, "box")
            self._add(self._uniq(None, f"{root}_{gname}"), g, {}, "imported",
                      self._material({"material": a.get("material") or {"preset": "plastic", "color": "#a0a6ae"}}),
                      parent=root)
        return {"object": root, "children": len(self._children(root))}

    def op_add_mesh_data(self, a):
        """Raw mesh (vertices + triangle indices) — for LLMs that compute custom geometry."""
        v = np.asarray(a.get("vertices") or [], float)
        f = np.asarray(a.get("faces") or [], np.int64)
        if v.ndim != 2 or v.shape[1] != 3 or f.ndim != 2 or f.shape[1] != 3 or len(v) < 3:
            raise EngineError("vertices must be [[x,y,z],...] and faces [[i,j,k],...]")
        if len(v) > 50000 or f.max() >= len(v) or f.min() < 0:
            raise EngineError("invalid face indices or too many vertices (max 50k)")
        m = M.uv_project(trimesh.Trimesh(v, f, process=True), "box")
        n = self._uniq(a.get("name"), "Mesh")
        self._add(n, m, a, "mesh")
        return {"object": n, "triangles": len(m.faces)}

    # ------------------------------------------------------------ queries
    def query(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool == "measure":
            names = self._targets(args.get("target"))
            if not names:
                return {"triangles": 0}
            ms = [self._world_mesh(n) for n in names]
            allv = np.vstack([m.vertices for m in ms])
            lo, hi = allv.min(0), allv.max(0)
            return {"size_m": [round(float(x), 4) for x in (hi - lo)], "min": lo.round(4).tolist(), "max": hi.round(4).tolist(),
                    "triangles": int(sum(len(m.faces) for m in ms)), "vertices": int(sum(len(m.vertices) for m in ms)),
                    "objects": len(ms)}
        if tool == "scene_stats":
            return self.query("measure", {"target": "*"}) | {"materials": sorted({
                f"{p['material']['preset']}:{p['material'].get('color', '')}" for p in self.parts.values() if p["material"]})}
        if tool == "list_material_presets":
            return {"presets": {k: {kk: vv for kk, vv in v.items() if kk != "pattern"} for k, v in PRESETS.items()}}
        raise EngineError(f"Unknown query '{tool}'")

    # ------------------------------------------------------------ output
    def objects(self) -> list[dict[str, Any]]:
        out = []
        for n, p in self.parts.items():
            o = {"name": n, "kind": p["kind"], "parent": p["parent"]}
            if p["mesh"] is not None:
                o["triangles"] = int(len(p["mesh"].faces))
                ext = p["mesh"].extents * np.abs(p["scale"])
                o["dims"] = [round(float(x), 4) for x in ext]
                o["material"] = p["material"].get("preset")
                o["color"] = p["material"].get("color") or PRESETS[p["material"]["preset"]].get("color")
            out.append(o)
        return out

    def _pbr(self, mat: dict, res: int, cache: dict):
        key = (tuple(sorted((k, str(v)) for k, v in mat.items())), res)
        if key in cache:
            return cache[key]
        preset = mat.get("preset", "plastic")
        tex = generate(preset, mat.get("color") or PRESETS[preset].get("color", "#808080"), res,
                       7, mat.get("roughness"), mat.get("metallic"))
        opacity = float(mat.get("opacity", tex["opacity"]))
        base_img = tex["baseColor"]
        alpha_mode = "BLEND" if opacity < 0.999 else "OPAQUE"
        tile = tex["tile"]
        image_att = mat.get("decal") or mat.get("image")
        if image_att:
            try:
                base_img = load_rgba(self.ctx.attachment_path(image_att), res)
            except Exception:
                base_img = Image.new("RGBA", (4, 4), (255, 0, 255, 255))
            tile = 1.0
            if mat.get("decal"):
                alpha_mode = "BLEND"
        emissive = float(mat.get("emissive", tex["emissive"]) or 0)
        col = tex["baseColor"].getpixel((0, 0)) if emissive else None
        em_rgb = None
        if emissive:
            from .textures import hex_rgb

            em_rgb = (hex_rgb(mat.get("color") or "#ffffff") * min(emissive, 1.0)).tolist()
        pbr = trimesh.visual.material.PBRMaterial(
            name=f"{preset}_{(mat.get('color') or '').lstrip('#')}_{zlib.crc32(repr(key).encode()) % 10**6:06d}",
            baseColorTexture=base_img, baseColorFactor=[1, 1, 1, opacity],
            metallicRoughnessTexture=tex["orm"] if not image_att else None,
            occlusionTexture=tex["orm"] if not image_att else None,
            normalTexture=tex["normal"] if not image_att else None,
            roughnessFactor=1.0 if not image_att else float(mat.get("roughness", 0.4)),
            metallicFactor=1.0 if not image_att else 0.0,
            emissiveFactor=em_rgb, alphaMode=alpha_mode, doubleSided=bool(mat.get("decal")) or opacity < 1)
        del col
        cache[key] = (pbr, tile)
        return cache[key]

    def to_trimesh_scene(self, hidden: set[str] | None = None, texture_res: int | None = None, quality: str = "normal",
                         only: list[str] | None = None, include_lods: bool = False):
        res = texture_res or self.texture_res
        scene = trimesh.Scene()
        cache: dict = {}
        hidden = set(hidden or ())
        # hidden parents hide their children
        hide_all = set()
        for h in hidden:
            if h in self.parts:
                hide_all |= {h, *self._children(h)}
        keep = None
        if only:
            keep = set()
            for o in only:
                if o in self.parts:
                    keep |= {o, *self._children(o)}
                    q = self.parts[o]["parent"]
                    while q:
                        keep.add(q)
                        q = self.parts[q]["parent"]
        for n, p in self.parts.items():
            if n in hide_all or (keep is not None and n not in keep):
                continue
            parent = p["parent"] if p["parent"] and p["parent"] not in hide_all and (keep is None or p["parent"] in keep) else None
            T = trs(p["position"], p["rotation"], p["scale"])
            if p["mesh"] is None:
                scene.graph.update(frame_from=parent or scene.graph.base_frame, frame_to=n, matrix=T)
                continue
            pbr, tile = self._pbr(p["material"], res, cache)
            meshes = [(n, p["mesh"])]
            if include_lods and p["meta"].get("lods"):
                meshes = [(f"{n}_LOD0", p["mesh"])] + [(f"{n}_LOD{i + 1}", lm) for i, lm in enumerate(p["meta"]["lods"]) if lm is not None]
            for nm, mesh in meshes:
                g = mesh.copy()
                vn = np.array(mesh.vertex_normals)
                uv = g.visual.uv if hasattr(g.visual, "uv") and g.visual.uv is not None else np.zeros((len(g.vertices), 2))
                g.visual = trimesh.visual.TextureVisuals(uv=uv / tile, material=pbr)
                g.vertex_normals = vn  # keep smooth normals (setting visuals clears trimesh's cache)
                if nm != n:
                    scene.graph.update(frame_from=parent or scene.graph.base_frame, frame_to=n, matrix=T) if n not in scene.graph.nodes else None
                    scene.add_geometry(g, node_name=nm, geom_name=nm, parent_node_name=n)
                else:
                    scene.add_geometry(g, node_name=nm, geom_name=nm, parent_node_name=parent, transform=T)
        return scene


_S = {"type": "string"}
_N = {"type": "number"}
_I = {"type": "integer"}
_B = {"type": "boolean"}
_MAT = {"description": "Material: preset name or {preset, color:#hex, roughness, metallic, emissive, opacity}. Presets: "
                       + ", ".join(PRESETS), "anyOf": [{"type": "string"}, {"type": "object"}]}
_COMMON = {"name": _S, "position": vec3("[x,y,z] metres (Y up), relative to parent"),
           "rotation": vec3("Euler degrees XYZ"), "scale": {"description": "number or [x,y,z]"}, "parent": _S,
           "material": _MAT, "color": color_prop("shortcut for material.color")}

GameEngine.tools = {t.name: t for t in [
    ToolSpec("set_texture_resolution", "Resolution of generated PBR texture maps (256|512|1024|2048).",
             {"resolution": _I}, ["resolution"]),
    ToolSpec("add_group", "Create an empty group node (for hierarchy / pivots).", {**_COMMON}, []),
    ToolSpec("add_primitive", "Add a UV-mapped primitive. shape: box|rounded_box|cylinder|cone|sphere|capsule|torus|wedge|plane. "
                              "size=[w,h,d] for box/rounded_box/wedge/plane; radius/height/radius_top for cylinder; "
                              "radius for sphere; radius+length for capsule; radius+tube_radius for torus; bevel for box.",
             {**_COMMON, "shape": {"type": "string", "enum": ["box", "rounded_box", "cylinder", "cone", "sphere", "capsule",
                                                              "torus", "wedge", "plane"]},
              "size": vec3(), "radius": _N, "radius_top": _N, "height": _N, "length": _N, "tube_radius": _N,
              "bevel": _N, "segments": _I, "subdivisions": _I}, ["shape"]),
    ToolSpec("add_lathe", "Revolve a [radius, y] profile around Y (bottles, vases, wheels, columns, bodies of revolution).",
             {**_COMMON, "profile": {"type": "array", "items": {"type": "array", "items": _N}}, "segments": _I, "angle": _N},
             ["profile"]),
    ToolSpec("add_loft", "Smooth organic/hard-surface body lofted along X through superellipse cross-sections. Each section: "
                         "{x, width (Z extent), height (Y extent), y (centre height), z (centre offset), exp_top, exp_bottom "
                         "(2=ellipse, 4+=boxy)}. Ideal for car bodies, fuselages, boats, torsos, bottles.",
             {**_COMMON, "sections": {"type": "array", "items": {"type": "object"}}, "segments": _I, "smoothness": _I},
             ["sections"]),
    ToolSpec("add_extrusion", "Extrude a 2D polygon [[x,y],...] along +Z by depth.",
             {**_COMMON, "points": {"type": "array", "items": {"type": "array", "items": _N}}, "depth": _N}, ["points", "depth"]),
    ToolSpec("add_tube", "Sweep a circular tube along a 3D path (pipes, frames, handles, cables).",
             {**_COMMON, "path": {"type": "array", "items": {"type": "array", "items": _N}}, "radius": _N, "segments": _I,
              "closed": _B}, ["path", "radius"]),
    ToolSpec("add_wheel", "Composite detailed wheel (tyre with tread, multi-spoke rim, brake disc, caliper). Axle along Z. "
                          "side=+1 for the +Z side of a vehicle, -1 for -Z.",
             {**_COMMON, "radius": _N, "width": _N, "spokes": _I, "rim_ratio": _N, "side": _N, "rim_material": _MAT,
              "tire_material": _MAT, "caliper_color": color_prop()}, []),
    ToolSpec("add_humanoid", "Composite stylised humanoid character (head, hair, eyes, torso, limbs, shoes) with colours "
                             "{skin, hair, shirt, pants, shoes, eyes}.",
             {**_COMMON, "height": _N, "build": _N, "colors": {"type": "object"}, "shirt_material": _S}, []),
    ToolSpec("add_tree", "Composite tree (broadleaf|conifer).",
             {**_COMMON, "height": _N, "kind": {"type": "string", "enum": ["broadleaf", "conifer"]}, "leaf_color": color_prop(),
              "seed": _I}, []),
    ToolSpec("add_terrain", "Rolling terrain heightfield.", {**_COMMON, "size": _N, "height": _N, "seed": _I, "subdivisions": _I}, []),
    ToolSpec("set_material", "Change material of an object (and its children by default). Optional only_kind filter "
                             "(e.g. 'rim', 'tire', 'spoke'). image = attachment id used as albedo texture.",
             {"target": _S, "material": _MAT, "color": color_prop(), "roughness": _N, "metallic": _N, "emissive": _N,
              "opacity": _N, "image": _S, "include_children": _B, "only_kind": _S}, ["target"]),
    ToolSpec("transform", "Set position/rotation/scale (absolute), translate (relative) or re-parent an object.",
             {"target": _S, "position": vec3(), "translate": vec3(), "rotation": vec3(), "scale": {"description": "number or [x,y,z]"},
              "parent": _S}, ["target"]),
    ToolSpec("duplicate", "Duplicate an object (with children).", {"target": _S, "name": _S, "position": vec3(), "translate": vec3()},
             ["target"]),
    ToolSpec("mirror", "Mirror-duplicate an object across an axis through the parent origin (symmetry).",
             {"target": _S, "axis": {"type": "string", "enum": ["x", "y", "z"]}, "name": _S, "mirror_geometry": _B}, ["target"]),
    ToolSpec("array", "Create count-1 copies with a constant offset/rotation step.",
             {"target": _S, "count": _I, "offset": vec3(), "rotation_step": vec3()}, ["target", "count"]),
    ToolSpec("delete", "Delete an object and its children.", {"target": _S}, ["target"]),
    ToolSpec("rename", "Rename an object.", {"target": _S, "new_name": _S}, ["target", "new_name"]),
    ToolSpec("boolean", "Mesh boolean (manifold): difference|union|intersection; tool object is consumed.",
             {"target": _S, "tool": _S, "operation": {"type": "string", "enum": ["difference", "union", "intersection"]},
              "keep_tool": _B}, ["target", "tool"]),
    ToolSpec("smooth", "Loop-subdivide a mesh for smoother organic surfaces (max 3 iterations).",
             {"target": _S, "iterations": _I}, ["target"]),
    ToolSpec("uv_unwrap", "Regenerate UVs by projection: box|cylindrical|spherical.",
             {"target": _S, "method": {"type": "string", "enum": ["box", "cylindrical", "spherical"]}, "scale": _N}, ["target"]),
    ToolSpec("decimate", "Reduce polygons (ratio = fraction of triangles to keep). target '*' = whole scene.",
             {"target": _S, "ratio": _N}, ["ratio"]),
    ToolSpec("generate_lods", "Generate LOD meshes (exported as *_LOD0/1/2).", {"target": _S, "ratios": {"type": "array", "items": _N}}, []),
    ToolSpec("optimize_for", "Optimise the asset for a platform: roblox|unity|unreal|web|mobile.",
             {"platform": {"type": "string", "enum": ["roblox", "unity", "unreal", "web", "mobile"]}}, ["platform"]),
    ToolSpec("apply_decal", "Project an uploaded image onto an object's surface (logo on a door, label on a bottle). "
                            "side = object-local axis the decal faces (±x/±y/±z); center=[u,v] 0..1 within the object's "
                            "bounds on that side; width/height metres; rotation deg.",
             {"target": _S, "attachment_id": _S, "side": _S, "center": {"type": "array", "items": _N}, "width": _N,
              "height": _N, "rotation": _N, "name": _S}, ["target", "attachment_id"]),
    ToolSpec("import_mesh", "Import an uploaded mesh file (GLB/GLTF/OBJ/STL/PLY) as objects.",
             {"attachment_id": _S, "name": _S, "import_scale": _N, "position": vec3(), "material": _MAT}, ["attachment_id"]),
    ToolSpec("add_mesh_data", "Add a custom mesh from raw vertices/faces (metres).",
             {**_COMMON, "vertices": {"type": "array", "items": {"type": "array", "items": _N}},
              "faces": {"type": "array", "items": {"type": "array", "items": _I}}}, ["vertices", "faces"]),
    ToolSpec("measure", "Bounding size (m), triangle and vertex counts of an object or '*'.", {"target": _S}, [], mutating=False),
    ToolSpec("scene_stats", "Scene totals and materials in use.", {}, [], mutating=False),
    ToolSpec("list_material_presets", "List PBR material presets.", {}, [], mutating=False),
]}
