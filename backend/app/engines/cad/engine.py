"""CAD pipeline: real B-Rep solid modeling on OpenCascade (through CadQuery).

Every operation is a parametric feature recorded in the project history; the model is the result of
replaying that history, so any parameter can be edited later ("make it 5 mm taller")."""
from __future__ import annotations

import math
import re
from typing import Any

import cadquery as cq
import numpy as np
import trimesh

from ..base import Engine, EngineError, ToolSpec, color_prop, vec3
from ..imaging import image_polygons_mm

SELECTOR_RE = re.compile(r"^[<>|#+\-XYZxyz()\s,andortnot%PLANECYLINDERLINECIRCLE0-9.\[\]]{1,60}$")
DEFAULT_COLOR = "#b8bec6"
PLANES = {"XY", "XZ", "YZ"}


def _sel(s: str | None, default: str) -> str:
    s = (s or default).strip()
    if not SELECTOR_RE.match(s):
        raise EngineError(f"Invalid face/edge selector '{s}'. Use CadQuery selectors like >Z, <Z, |Z, >X.")
    return s


def _num(args: dict, key: str, default: float | None = None, minv: float | None = None) -> float:
    v = args.get(key, default)
    if v is None:
        raise EngineError(f"Missing numeric parameter '{key}'.")
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise EngineError(f"Parameter '{key}' must be a number.") from None
    if minv is not None and v < minv:
        raise EngineError(f"Parameter '{key}' must be ≥ {minv} (got {v}).")
    return v


def _v3(v, default=(0, 0, 0)) -> tuple[float, float, float]:
    if v is None:
        return tuple(default)
    if len(v) != 3:
        raise EngineError("Vectors must have 3 components [x, y, z].")
    return tuple(float(x) for x in v)


def _face_plane(face: cq.Face) -> cq.Plane:
    if face.geomType() != "PLANE":
        raise EngineError("This operation needs a planar face (selected face is curved).")
    n = face.normalAt()
    c = face.Center()
    up = cq.Vector(0, 0, 1)
    if abs(n.dot(up)) > 0.99:
        xdir = cq.Vector(1, 0, 0)
    else:
        xdir = up.cross(n).normalized() * -1  # x along the face horizontally
        if xdir.Length < 1e-6:
            xdir = cq.Vector(1, 0, 0)
    return cq.Plane(origin=c, xDir=xdir, normal=n)


def exact_bbox(shape: cq.Shape) -> cq.BoundBox:
    """Tight bounding box from exact geometry (OCC's default box is inflated by tolerances/triangulation)."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    b = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape.wrapped, b, False, False)
    return cq.BoundBox(b)


def _unwrap(shape: cq.Shape) -> cq.Shape:
    if isinstance(shape, cq.Compound):
        solids = shape.Solids()
        if len(solids) == 1:
            return solids[0]
    return shape


def _bool(a: cq.Shape, b: cq.Shape, op: str) -> cq.Shape:
    wa, wb = cq.Workplane().add(_unwrap(a)), cq.Workplane().add(_unwrap(b))
    res = {"add": wa.union, "union": wa.union, "cut": wa.cut, "intersect": wa.intersect}[op](wb, clean=True)
    vals = [v for v in res.vals() if isinstance(v, cq.Shape)]
    if not vals:
        raise EngineError(f"Boolean {op} produced no geometry")
    out = vals[0] if len(vals) == 1 else cq.Compound.makeCompound(vals)
    return _unwrap(out)


class CadEngine(Engine):
    mode = "cad"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.reset()

    def reset(self) -> None:
        self.bodies: dict[str, dict[str, Any]] = {}

    # ---------------------------------------------------------------- helpers
    def _body(self, name: str) -> dict[str, Any]:
        if name not in self.bodies:
            raise EngineError(f"Body '{name}' does not exist. Existing: {', '.join(self.bodies) or 'none'}.")
        return self.bodies[name]

    def _new_name(self, name: str | None, base: str) -> str:
        if name and name not in self.bodies:
            return name
        base = name or base
        i = 1
        while f"{base}_{i}" in self.bodies:
            i += 1
        return f"{base}_{i}"

    def _put(self, name: str, shape: cq.Shape, color: str | None = None, keep_color_of: str | None = None,
             meta: dict | None = None) -> None:
        shape = _unwrap(shape) if shape is not None else None
        if shape is None or not shape.isValid():
            raise EngineError("The geometry kernel produced an invalid solid (check the parameters).")
        prev = self.bodies.get(keep_color_of or name)
        self.bodies[name] = {
            "shape": shape,
            "color": color or (prev["color"] if prev else DEFAULT_COLOR),
            "meta": meta or (prev["meta"] if prev else {}),
        }

    @staticmethod
    def _solid(wp: cq.Workplane) -> cq.Shape:
        vals = [v for v in wp.vals() if isinstance(v, cq.Shape)]
        if not vals:
            raise EngineError("Operation produced no solid.")
        return vals[0] if len(vals) == 1 else cq.Compound.makeCompound(vals)

    def _combine(self, args: dict, new_shape: cq.Shape, default_name: str, color: str | None) -> dict:
        op = args.get("operation", "new")
        if op == "new":
            name = self._new_name(args.get("name"), default_name)
            self._put(name, new_shape, color)
            return {"body": name}
        target = self._body(args.get("target") or "")
        base = target["shape"]
        try:
            if op not in ("add", "cut", "intersect"):
                raise EngineError(f"Unknown operation '{op}' (new|add|cut|intersect).")
            res = _bool(base, new_shape, op)
        except EngineError:
            raise
        except Exception as e:  # kernel failure
            raise EngineError(f"Boolean {op} failed: {e}") from e
        self._put(args["target"], res)
        return {"body": args["target"]}

    # ---------------------------------------------------------------- sketch
    def _sketch(self, sk: dict, plane: cq.Plane) -> cq.Workplane:
        t = sk.get("type", "rect")
        wp = cq.Workplane(plane)
        cx, cy = (sk.get("center") or [0, 0])[:2]
        wp = wp.center(float(cx), float(cy))
        if t == "rect":
            wp = wp.rect(_num(sk, "width", minv=1e-3), _num(sk, "height", minv=1e-3))
            if sk.get("corner_radius"):
                # rounded rectangle via 2D sketch
                r = _num(sk, "corner_radius", minv=0)
                s = cq.Sketch().rect(_num(sk, "width"), _num(sk, "height")).vertices().fillet(r)
                return cq.Workplane(plane).center(float(cx), float(cy)).placeSketch(s)
        elif t == "circle":
            wp = wp.circle(_num(sk, "radius", minv=1e-3))
        elif t == "regular_polygon":
            wp = wp.polygon(int(sk.get("sides", 6)), 2 * _num(sk, "radius", minv=1e-3))
        elif t == "slot":
            wp = wp.slot2D(_num(sk, "length", minv=1e-3), _num(sk, "width", minv=1e-3), float(sk.get("angle", 0)))
        elif t == "polygon":
            pts = sk.get("points") or []
            if len(pts) < 3:
                raise EngineError("Polygon sketch needs at least 3 points.")
            wp = wp.polyline([tuple(map(float, p[:2])) for p in pts]).close()
        elif t == "text":
            raise EngineError("Use the add_text tool for text.")
        else:
            raise EngineError(f"Unknown sketch type '{t}' (rect|circle|regular_polygon|slot|polygon).")
        return wp

    @staticmethod
    def _plane(name: str | None, offset: float = 0.0) -> cq.Plane:
        name = (name or "XY").upper()
        if name not in PLANES:
            raise EngineError("plane must be XY, XZ or YZ")
        p = cq.Plane.named(name)
        return p.translated(p.zDir * offset) if offset else p

    # ---------------------------------------------------------------- apply
    def apply(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        fn = getattr(self, f"op_{tool}", None)
        if fn is None or tool not in self.tools or not self.tools[tool].mutating:
            raise EngineError(f"Unknown CAD tool '{tool}'.")
        try:
            return fn(args) or {}
        except EngineError:
            raise
        except Exception as e:
            raise EngineError(f"{tool} failed in the CAD kernel: {e}") from e

    def op_create_box(self, a):
        w, d, h = _num(a, "width", minv=1e-3), _num(a, "depth", minv=1e-3), _num(a, "height", minv=1e-3)
        x, y, z = _v3(a.get("position"))
        wp = cq.Workplane("XY").box(w, d, h, centered=(True, True, False))
        if a.get("edge_radius"):
            wp = wp.edges().fillet(min(_num(a, "edge_radius"), min(w, d, h) / 2 - 1e-3))
        shape = self._solid(wp).moved(cq.Location(cq.Vector(x, y, z)))
        return self._combine(a, shape, "Box", a.get("color"))

    def op_create_cylinder(self, a):
        r, h = _num(a, "radius", minv=1e-3), _num(a, "height", minv=1e-3)
        axis = (a.get("axis") or "Z").upper()
        plane = {"Z": "XY", "Y": "XZ", "X": "YZ"}.get(axis, "XY")
        wp = cq.Workplane(plane).circle(r).extrude(h)
        shape = self._solid(wp).moved(cq.Location(cq.Vector(*_v3(a.get("position")))))
        return self._combine(a, shape, "Cylinder", a.get("color"))

    def op_create_sphere(self, a):
        r = _num(a, "radius", minv=1e-3)
        shape = cq.Solid.makeSphere(r, cq.Vector(*_v3(a.get("position"))), angleDegrees1=-90, angleDegrees2=90)
        return self._combine(a, shape, "Sphere", a.get("color"))

    def op_extrude_sketch(self, a):
        plane = self._plane(a.get("plane"), float(a.get("plane_offset", 0)))
        dist = _num(a, "distance")
        wp = self._sketch(a.get("sketch") or {}, plane)
        wp = wp.extrude(dist, taper=float(a.get("taper", 0)) or None) if a.get("taper") else wp.extrude(dist)
        return self._combine(a, self._solid(wp), "Extrude", a.get("color"))

    def op_revolve(self, a):
        pts = a.get("profile") or []
        if len(pts) < 3:
            raise EngineError("revolve needs a profile of ≥3 [radius, z] points.")
        angle = float(a.get("angle", 360))
        wp = cq.Workplane("XZ").polyline([(float(p[0]), float(p[1])) for p in pts]).close()
        wp = wp.revolve(angle, (0, 0, 0), (0, 1, 0))
        shape = self._solid(wp).moved(cq.Location(cq.Vector(*_v3(a.get("position")))))
        return self._combine(a, shape, "Revolve", a.get("color"))

    def op_boolean(self, a):
        tgt, tl = self._body(a.get("target", "")), self._body(a.get("tool", ""))
        op = a.get("operation", "cut")
        if op not in ("union", "cut", "intersect"):
            raise EngineError("operation must be union|cut|intersect")
        self._put(a["target"], _bool(tgt["shape"], tl["shape"], op))
        if not a.get("keep_tool", False):
            del self.bodies[a["tool"]]
        return {"body": a["target"]}

    def op_fillet(self, a):
        b = self._body(a.get("target", ""))
        r = _num(a, "radius", minv=1e-3)
        sel = a.get("edges", "all")
        wp = cq.Workplane().add(b["shape"])
        wp = wp.edges() if sel == "all" else wp.edges(_sel(sel, "|Z"))
        try:
            self._put(a["target"], self._solid(wp.fillet(r)))
        except Exception as e:
            raise EngineError(f"Fillet r={r} failed (radius too large for the selected edges?): {e}") from e
        return {"body": a["target"]}

    def op_chamfer(self, a):
        b = self._body(a.get("target", ""))
        d = _num(a, "distance", minv=1e-3)
        sel = a.get("edges", "all")
        wp = cq.Workplane().add(b["shape"])
        wp = wp.edges() if sel == "all" else wp.edges(_sel(sel, ">Z"))
        try:
            self._put(a["target"], self._solid(wp.chamfer(d)))
        except Exception as e:
            raise EngineError(f"Chamfer {d} failed: {e}") from e
        return {"body": a["target"]}

    def op_shell(self, a):
        b = self._body(a.get("target", ""))
        t = _num(a, "thickness", minv=1e-3)
        open_face = a.get("open_face", ">Z")
        shape = b["shape"]
        try:
            wp = cq.Workplane().add(shape)
            if open_face in (None, "", "none"):
                res = self._solid(wp.shell(-t))
            else:
                res = self._solid(wp.faces(_sel(open_face, ">Z")).shell(-t))
        except Exception as e:
            raise EngineError(f"Shell {t} mm failed: {e}") from e
        self._put(a["target"], res, meta={**b["meta"], "wall": t})
        return {"body": a["target"]}

    def op_hole(self, a):
        b = self._body(a.get("target", ""))
        dia = _num(a, "diameter", minv=1e-3)
        face_sel = _sel(a.get("face"), ">Z")
        faces = cq.Workplane().add(b["shape"]).faces(face_sel).vals()
        if not faces:
            raise EngineError(f"No face matches selector {face_sel}.")
        plane = _face_plane(faces[0])
        bb = b["shape"].BoundingBox()
        depth = a.get("depth")
        depth = float(depth) if depth not in (None, "through", 0) else bb.DiagonalLength * 2
        positions = a.get("positions")
        if not positions:
            inset = float(a.get("corner_inset", 8))
            fb = faces[0].BoundingBox()
            # local half-extents of the face in its own plane
            pts = [plane.toLocalCoords(v.Center()) for v in faces[0].Vertices()]
            xs, ys = [p.x for p in pts], [p.y for p in pts]
            hx, hy = (max(xs) - min(xs)) / 2 - inset, (max(ys) - min(ys)) / 2 - inset
            if hx <= 0 or hy <= 0 or fb is None:
                raise EngineError("corner_inset too large for this face.")
            positions = [[-hx, -hy], [hx, -hy], [hx, hy], [-hx, hy]]
        cut = None
        for p in positions:
            origin = plane.toWorldCoords((float(p[0]), float(p[1]), 0.0))
            cyl = cq.Solid.makeCylinder(dia / 2, depth, origin + plane.zDir * 0.01, plane.zDir * -1)
            if a.get("countersink_diameter"):
                cs = float(a["countersink_diameter"]) / 2
                cone = cq.Solid.makeCone(cs, 0, cs, origin + plane.zDir * 0.001, plane.zDir * -1)
                cyl = _bool(cyl, cone, "add")
            cut = cyl if cut is None else _bool(cut, cyl, "add")
        self._put(a["target"], _bool(b["shape"], cut, "cut"))
        return {"body": a["target"], "holes": len(positions)}

    def op_pattern(self, a):
        src = self._body(a.get("source", ""))
        count = int(a.get("count", 2))
        if not 2 <= count <= 200:
            raise EngineError("count must be between 2 and 200")
        kind = a.get("type", "linear")
        copies = []
        for i in range(1, count):
            if kind == "linear":
                sx, sy, sz = _v3(a.get("spacing"), (10, 0, 0))
                copies.append(src["shape"].moved(cq.Location(cq.Vector(sx * i, sy * i, sz * i))))
            elif kind == "circular":
                ang = float(a.get("total_angle", 360))
                step = ang / (count if abs(ang - 360) < 1e-6 else count - 1)
                c = cq.Vector(*_v3(a.get("center")))
                ax = cq.Vector(*_v3(a.get("axis"), (0, 0, 1)))
                copies.append(src["shape"].rotate(c, c + ax, step * i))
            else:
                raise EngineError("type must be linear|circular")
        mode = a.get("combine", "union")
        if mode == "union":
            res = src["shape"]
            for c in copies:
                res = _bool(res, c, "add")
            self._put(a["source"], res)
            return {"body": a["source"]}
        names = []
        for c in copies:
            n = self._new_name(None, a["source"])
            self._put(n, c, src["color"])
            names.append(n)
        return {"bodies": names}

    def op_mirror(self, a):
        b = self._body(a.get("target", ""))
        plane = (a.get("plane") or "YZ").upper()
        if plane not in PLANES:
            raise EngineError("plane must be XY, XZ or YZ")
        m = b["shape"].mirror(plane, _v3(a.get("origin")))
        if a.get("union", True):
            self._put(a["target"], _bool(b["shape"], m, "add"))
            return {"body": a["target"]}
        n = self._new_name(a.get("name"), a["target"] + "_mirror")
        self._put(n, m, b["color"])
        return {"body": n}

    def op_transform(self, a):
        b = self._body(a.get("target", ""))
        s = b["shape"]
        rot = _v3(a.get("rotation"))
        for axis, ang in zip(((1, 0, 0), (0, 1, 0), (0, 0, 1)), rot):
            if ang:
                c = s.Center()
                s = s.rotate(c, c + cq.Vector(*axis), ang)
        t = _v3(a.get("translate"))
        if any(t):
            s = s.moved(cq.Location(cq.Vector(*t)))
        if a.get("scale") and float(a["scale"]) != 1:
            s = s.scale(float(a["scale"]))
        self._put(a["target"], s)
        return {"body": a["target"]}

    def op_set_color(self, a):
        b = self._body(a.get("target", ""))
        col = a.get("color", "")
        if not re.match(r"^#[0-9a-fA-F]{6}$", col):
            raise EngineError("color must be #RRGGBB")
        b["color"] = col
        return {"body": a["target"]}

    def op_rename(self, a):
        b = self._body(a.get("target", ""))
        new = a.get("new_name", "").strip()
        if not new or new in self.bodies:
            raise EngineError("new_name must be unique and non-empty")
        self.bodies = {(new if k == a["target"] else k): v for k, v in self.bodies.items()}
        return {"body": new}

    def op_delete_body(self, a):
        self._body(a.get("target", ""))
        del self.bodies[a["target"]]
        return {"deleted": a["target"]}

    def op_duplicate(self, a):
        b = self._body(a.get("target", ""))
        n = self._new_name(a.get("name"), a["target"] + "_copy")
        self._put(n, b["shape"].moved(cq.Location(cq.Vector(*_v3(a.get("translate"))))), b["color"])
        return {"body": n}

    def _place_profile_solids(self, polys, a, plane: cq.Plane, depth: float, outward: bool) -> cq.Shape:
        solids = []
        rot = math.radians(float(a.get("rotation", 0)))
        ox, oy = (a.get("offset") or [0, 0])[:2]
        ca, sa = math.cos(rot), math.sin(rot)

        def tf(x, y):
            return (x * ca - y * sa + float(ox), x * sa + y * ca + float(oy))

        for p in polys:
            outer = cq.Wire.makePolygon([cq.Vector(*tf(x, y), 0) for x, y in list(p.exterior.coords)[:-1]], close=True)
            inner = [cq.Wire.makePolygon([cq.Vector(*tf(x, y), 0) for x, y in list(i.coords)[:-1]], close=True)
                     for i in p.interiors]
            face = cq.Face.makeFromWires(outer, inner)
            z0 = 0.0 if outward else -depth
            solid = cq.Solid.extrudeLinear(face.moved(cq.Location(cq.Vector(0, 0, z0))), cq.Vector(0, 0, depth + (0 if outward else 0.01)))
            solids.append(solid)
        comp = cq.Compound.makeCompound(solids)
        return comp.moved(cq.Location(plane))

    def op_apply_image(self, a):
        """Emboss / engrave a 2D attachment (logo) onto a planar face."""
        b = self._body(a.get("target", ""))
        att = a.get("attachment_id")
        if not att:
            raise EngineError("attachment_id is required (upload an image first).")
        path = self.ctx.attachment_path(att)
        face_sel = _sel(a.get("face"), ">Z")
        faces = cq.Workplane().add(b["shape"]).faces(face_sel).vals()
        if not faces:
            raise EngineError(f"No face matches {face_sel}")
        plane = _face_plane(faces[0])
        fbb = faces[0].BoundingBox()
        size = float(a.get("width") or max(5.0, min(fbb.xlen or fbb.zlen, fbb.ylen or fbb.zlen) * 0.5))
        try:
            polys = image_polygons_mm(path, size, int(a.get("resolution", 180)), a.get("threshold"),
                                      bool(a.get("invert", False)))
        except ValueError as e:
            raise EngineError(str(e)) from e
        mode = a.get("mode", "emboss")
        depth = _num(a, "depth", 0.6, 0.05)
        if mode == "engrave":
            cutter = self._place_profile_solids(polys, a, plane, depth, outward=False)
            self._put(a["target"], _bool(b["shape"], cutter, "cut"))
            return {"body": a["target"], "shapes": len(polys)}
        if mode != "emboss":
            raise EngineError("mode must be emboss|engrave")
        logo = self._place_profile_solids(polys, a, plane, depth, outward=True)
        if a.get("separate_body", True):
            n = self._new_name(a.get("name"), "Logo")
            self._put(n, logo, a.get("color") or "#1f6feb", meta={"on": a["target"]})
            return {"body": n, "shapes": len(polys)}
        self._put(a["target"], _bool(b["shape"], logo, "add"))
        return {"body": a["target"], "shapes": len(polys)}

    def op_add_text(self, a):
        b = self._body(a.get("target", ""))
        faces = cq.Workplane().add(b["shape"]).faces(_sel(a.get("face"), ">Z")).vals()
        if not faces:
            raise EngineError("No face matches the selector")
        plane = _face_plane(faces[0])
        ox, oy = (a.get("offset") or [0, 0])[:2]
        depth = _num(a, "depth", 0.6, 0.05)
        mode = a.get("mode", "emboss")
        wp = cq.Workplane(plane).center(float(ox), float(oy))
        txt = wp.text(str(a.get("text", "")) or "TEXT", _num(a, "size", 8, 0.5), depth if mode == "emboss" else -depth,
                      combine=False, halign="center", valign="center")
        shape = self._solid(txt)
        if mode == "engrave":
            self._put(a["target"], _bool(b["shape"], shape, "cut"))
            return {"body": a["target"]}
        n = self._new_name(a.get("name"), "Text")
        self._put(n, shape, a.get("color") or "#222222")
        return {"body": n}

    def op_import_step(self, a):
        path = self.ctx.attachment_path(a.get("attachment_id", ""))
        shape = cq.importers.importStep(path).val()
        n = self._new_name(a.get("name"), "Imported")
        self._put(n, shape, a.get("color"))
        return {"body": n}

    # ---------------------------------------------------------------- queries
    def query(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool == "measure":
            b = self._body(args.get("target", ""))
            s = b["shape"]
            bb = exact_bbox(s)
            com = s.Center()
            return {"body": args["target"], "size_mm": [round(bb.xlen, 3), round(bb.ylen, 3), round(bb.zlen, 3)],
                    "min": [round(bb.xmin, 3), round(bb.ymin, 3), round(bb.zmin, 3)],
                    "max": [round(bb.xmax, 3), round(bb.ymax, 3), round(bb.zmax, 3)],
                    "volume_mm3": round(s.Volume(), 3), "area_mm2": round(s.Area(), 3),
                    "center": [round(com.x, 3), round(com.y, 3), round(com.z, 3)],
                    "faces": len(s.Faces()), "edges": len(s.Edges()), "valid": s.isValid()}
        if tool == "measure_distance":
            a, b = self._body(args.get("a", "")), self._body(args.get("b", ""))
            return {"min_distance_mm": round(a["shape"].distance(b["shape"]), 4)}
        if tool == "list_faces":
            b = self._body(args.get("target", ""))
            out = []
            for sel in (">Z", "<Z", ">X", "<X", ">Y", "<Y"):
                f = cq.Workplane().add(b["shape"]).faces(sel).vals()
                if f:
                    c = f[0].Center()
                    out.append({"selector": sel, "type": f[0].geomType(), "area": round(f[0].Area(), 2),
                                "center": [round(c.x, 2), round(c.y, 2), round(c.z, 2)]})
            return {"faces": out}
        raise EngineError(f"Unknown query '{tool}'")

    # ---------------------------------------------------------------- output
    def objects(self) -> list[dict[str, Any]]:
        out = []
        for name, b in self.bodies.items():
            bb = exact_bbox(b["shape"])
            out.append({"name": name, "kind": "body", "color": b["color"],
                        "dims": [round(bb.xlen, 3), round(bb.ylen, 3), round(bb.zlen, 3)],
                        "volume": round(b["shape"].Volume(), 2), "meta": b["meta"]})
        return out

    def tessellate(self, name: str, tolerance: float | None = None) -> trimesh.Trimesh:
        s = self.bodies[name]["shape"]
        bb = s.BoundingBox()
        tol = tolerance or max(0.01, min(0.2, bb.DiagonalLength / 1500))
        verts, faces = [], []
        for f in s.Faces():
            v, t = f.tessellate(tol, 0.2)
            if not t:
                continue
            off = len(verts)
            verts.extend((p.x, p.y, p.z) for p in v)
            faces.extend((a + off, b + off, c + off) for a, b, c in t)
        m = trimesh.Trimesh(np.array(verts, dtype=np.float64).reshape(-1, 3),
                            np.array(faces, dtype=np.int64).reshape(-1, 3), process=False)
        return m

    def to_trimesh_scene(self, hidden: set[str] | None = None, texture_res: int = 1024, quality: str = "normal",
                         only: list[str] | None = None):
        tol = {"draft": 0.3, "normal": None, "fine": 0.01}.get(quality)
        scene = trimesh.Scene()
        for name, b in self.bodies.items():
            if hidden and name in hidden or (only and name not in only):
                continue
            m = self.tessellate(name, tol)
            col = trimesh.visual.color.hex_to_rgba(b["color"]) if b["color"] else [184, 190, 198, 255]
            lin = [((c / 255) / 12.92 if c / 255 <= 0.04045 else ((c / 255 + 0.055) / 1.055) ** 2.4) for c in col[:3]]
            m.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
                name=name, baseColorFactor=lin + [1.0], metallicFactor=0.05, roughnessFactor=0.55,
                doubleSided=False))
            scene.add_geometry(m, node_name=name, geom_name=name)
        return scene

    def brep_compound(self, only: list[str] | None = None) -> cq.Compound:
        shapes = [b["shape"] for n, b in self.bodies.items() if not only or n in only]
        if not shapes:
            raise EngineError("Nothing to export.")
        return cq.Compound.makeCompound(shapes)

    def brep_assembly(self, only: list[str] | None = None) -> cq.Assembly:
        asm = cq.Assembly(name="model")
        for n, b in self.bodies.items():
            if only and n not in only:
                continue
            asm.add(b["shape"], name=n, color=cq.Color(b["color"]))
        return asm


_S = {"type": "string"}
_N = {"type": "number"}
_B = {"type": "boolean"}
_OPS = {"type": "string", "enum": ["new", "add", "cut", "intersect"],
        "description": "new = create a new body; add/cut/intersect = combine with 'target'"}

CadEngine.tools = {t.name: t for t in [
    ToolSpec("create_box", "Create a rectangular block (mm). Base sits on Z=position.z, centred in XY.",
             {"name": _S, "width": {**_N, "description": "X size mm"}, "depth": {**_N, "description": "Y size mm"},
              "height": {**_N, "description": "Z size mm"}, "position": vec3("[x,y,z] of base centre, mm"),
              "edge_radius": {**_N, "description": "optional fillet on all edges"}, "operation": _OPS, "target": _S,
              "color": color_prop()}, ["width", "depth", "height"]),
    ToolSpec("create_cylinder", "Create a cylinder (mm) starting at position along +axis.",
             {"name": _S, "radius": _N, "height": _N, "axis": {"type": "string", "enum": ["X", "Y", "Z"]},
              "position": vec3(), "operation": _OPS, "target": _S, "color": color_prop()}, ["radius", "height"]),
    ToolSpec("create_sphere", "Create a sphere (mm).",
             {"name": _S, "radius": _N, "position": vec3(), "operation": _OPS, "target": _S, "color": color_prop()},
             ["radius"]),
    ToolSpec("extrude_sketch", "Draw a 2D sketch on a plane and extrude it (parametric).",
             {"name": _S, "plane": {"type": "string", "enum": ["XY", "XZ", "YZ"]}, "plane_offset": _N,
              "sketch": {"type": "object", "description": "{type: rect|circle|regular_polygon|slot|polygon, width, height, "
                                                         "corner_radius, radius, sides, length, points:[[x,y]...], center:[x,y]}"},
              "distance": _N, "taper": {**_N, "description": "draft angle deg"}, "operation": _OPS, "target": _S,
              "color": color_prop()}, ["sketch", "distance"]),
    ToolSpec("revolve", "Revolve a closed [radius, z] profile around the Z axis.",
             {"name": _S, "profile": {"type": "array", "items": {"type": "array", "items": _N}}, "angle": _N,
              "position": vec3(), "operation": _OPS, "target": _S, "color": color_prop()}, ["profile"]),
    ToolSpec("boolean", "Boolean between two bodies: union | cut | intersect (tool body is consumed).",
             {"target": _S, "tool": _S, "operation": {"type": "string", "enum": ["union", "cut", "intersect"]},
              "keep_tool": _B}, ["target", "tool", "operation"]),
    ToolSpec("fillet", "Round edges. edges = CadQuery selector (|Z vertical, >Z top, <Z bottom) or 'all'.",
             {"target": _S, "radius": _N, "edges": _S}, ["target", "radius"]),
    ToolSpec("chamfer", "Chamfer edges (selector or 'all').", {"target": _S, "distance": _N, "edges": _S},
             ["target", "distance"]),
    ToolSpec("shell", "Hollow a solid with given wall thickness, removing open_face (>Z default, 'none' = closed).",
             {"target": _S, "thickness": _N, "open_face": _S}, ["target", "thickness"]),
    ToolSpec("hole", "Drill holes into a planar face. positions are [x,y] in face-local mm coordinates (origin = face "
                     "centre). Omit positions for 4 corner holes at corner_inset. depth omitted = through.",
             {"target": _S, "face": _S, "diameter": _N, "depth": _N,
              "positions": {"type": "array", "items": {"type": "array", "items": _N}}, "corner_inset": _N,
              "countersink_diameter": _N}, ["target", "diameter"]),
    ToolSpec("pattern", "Linear or circular pattern of a body (union into it or create new bodies).",
             {"source": _S, "type": {"type": "string", "enum": ["linear", "circular"]}, "count": {"type": "integer"},
              "spacing": vec3("linear step"), "center": vec3(), "axis": vec3(), "total_angle": _N,
              "combine": {"type": "string", "enum": ["union", "new_bodies"]}}, ["source", "count"]),
    ToolSpec("mirror", "Mirror a body across XY/XZ/YZ (union with original by default).",
             {"target": _S, "plane": {"type": "string", "enum": ["XY", "XZ", "YZ"]}, "origin": vec3(), "union": _B,
              "name": _S}, ["target"]),
    ToolSpec("transform", "Move / rotate (deg, about body centre) / uniformly scale a body.",
             {"target": _S, "translate": vec3(), "rotation": vec3("degrees about X,Y,Z"), "scale": _N}, ["target"]),
    ToolSpec("set_color", "Set display/print colour of a body (exported to 3MF/STEP/GLB).",
             {"target": _S, "color": color_prop()}, ["target", "color"]),
    ToolSpec("rename", "Rename a body.", {"target": _S, "new_name": _S}, ["target", "new_name"]),
    ToolSpec("delete_body", "Delete a body.", {"target": _S}, ["target"]),
    ToolSpec("duplicate", "Duplicate a body with an offset.", {"target": _S, "translate": vec3(), "name": _S},
             ["target"]),
    ToolSpec("apply_image", "Emboss (raised, separate colour body by default) or engrave an uploaded image/logo onto "
                            "a planar face. width = logo width in mm, offset = [x,y] on face, rotation deg.",
             {"target": _S, "attachment_id": _S, "face": _S, "mode": {"type": "string", "enum": ["emboss", "engrave"]},
              "depth": _N, "width": _N, "offset": {"type": "array", "items": _N}, "rotation": _N, "invert": _B,
              "threshold": _N, "color": color_prop(), "separate_body": _B, "name": _S},
             ["target", "attachment_id"]),
    ToolSpec("add_text", "Emboss or engrave text on a planar face.",
             {"target": _S, "text": _S, "face": _S, "size": _N, "depth": _N,
              "mode": {"type": "string", "enum": ["emboss", "engrave"]}, "offset": {"type": "array", "items": _N},
              "color": color_prop(), "name": _S}, ["target", "text"]),
    ToolSpec("import_step", "Import a STEP file attachment as a B-Rep body.", {"attachment_id": _S, "name": _S},
             ["attachment_id"]),
    ToolSpec("measure", "Measure a body: bounding box, volume, area, validity.", {"target": _S}, ["target"],
             mutating=False),
    ToolSpec("measure_distance", "Minimum distance between two bodies (mm).", {"a": _S, "b": _S}, ["a", "b"],
             mutating=False),
    ToolSpec("list_faces", "List the main planar faces of a body with their selectors.", {"target": _S}, ["target"],
             mutating=False),
]}
