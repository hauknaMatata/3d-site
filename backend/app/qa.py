"""Technical quality control — measured by code, never by an LLM.

Every check returns one of: pass | warn | fail | unchecked | info.  "unchecked" means the system could
not measure it (it is never silently counted as passed). Category scores are computed only from checks
that actually ran; AI opinions are stored separately by the team module."""
from __future__ import annotations

import io
import time
from typing import Any

import logging
import tempfile

import numpy as np
import trimesh

logging.getLogger("trimesh").setLevel(logging.ERROR)

PRINT_BED_MM = (256.0, 256.0, 256.0)  # Bambu Lab X1/P1 build volume
MIN_WALL_FDM_MM = 0.8  # 2 perimeters of a 0.4 mm nozzle
SCORE = {"pass": 100, "warn": 60, "fail": 0}


def _check(cid: str, category: str, name: str, status: str, value: Any = None, expected: Any = None,
           detail: str = "", targets: list[str] | None = None) -> dict[str, Any]:
    return {"id": cid, "category": category, "name": name, "status": status, "value": value, "expected": expected,
            "detail": detail, "targets": targets or []}


def _mesh_topology(m: trimesh.Trimesh) -> dict[str, Any]:
    mm = trimesh.Trimesh(m.vertices.copy(), m.faces.copy(), process=False)
    mm.merge_vertices(merge_tex=True, merge_norm=True)
    degenerate = int((~mm.nondegenerate_faces()).sum())
    mm.update_faces(mm.nondegenerate_faces())
    edges = np.sort(mm.edges, axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return {"watertight": bool(mm.is_watertight), "winding": bool(mm.is_winding_consistent),
            "non_manifold_edges": int((counts > 2).sum()), "boundary_edges": int((counts == 1).sum()),
            "degenerate_faces": degenerate, "volume": float(mm.volume) if mm.is_watertight else None, "mesh": mm}


def _wall_thickness(mesh: trimesh.Trimesh, samples: int = 500) -> float | None:
    """Minimum wall thickness from inward ray casts at evenly sampled surface points (5th percentile)."""
    try:
        pts, fi = trimesh.sample.sample_surface_even(mesh, samples, seed=1)
        if len(pts) < 20:
            return None
        n = mesh.face_normals[fi]
        loc, ri, _ = mesh.ray.intersects_location(pts - n * 1e-4, -n, multiple_hits=False)
        if len(loc) == 0:
            return None
        d = np.linalg.norm(loc - pts[ri], axis=1)
        d = d[d > 1e-3]
        return float(np.percentile(d, 5)) if len(d) else None
    except Exception:  # noqa: BLE001
        return None


# ============================================================================ CAD
def qa_cad(rt) -> list[dict]:
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Check

    from .engines.cad.engine import exact_bbox

    eng = rt.engine
    hidden = set(rt.state.get("hidden", []))
    names = [n for n in eng.bodies if n not in hidden]
    checks: list[dict] = []
    if not names:
        return [_check("scene.empty", "Geometria", "Model zawiera bryły", "fail", 0, ">0", "Scena jest pusta")]

    # history / boolean correctness
    errs = rt.errors
    checks.append(_check("history.errors", "Operacje Boolean", "Wszystkie operacje z historii wykonane bez błędu",
                         "fail" if errs else "pass", len(errs), 0, "; ".join(list(errs.values())[:3])))

    invalid, selfint, not_solid = [], [], []
    meshes: dict[str, trimesh.Trimesh] = {}
    for n in names:
        shape = eng.bodies[n]["shape"]
        if not shape.isValid():
            invalid.append(n)
        try:
            chk = BRepAlgoAPI_Check(shape.wrapped, True, True)
            if not chk.IsValid():
                selfint.append(n)
        except Exception:  # noqa: BLE001
            pass
        if not shape.Solids():
            not_solid.append(n)
        meshes[n] = eng.tessellate(n)
    checks.append(_check("brep.valid", "Geometria", "Poprawność B-Rep (BRepCheck)", "fail" if invalid else "pass",
                         len(invalid), 0, ", ".join(invalid), invalid))
    checks.append(_check("brep.self_intersection", "Przecięcia", "Brak samoprzecięć / błędów topologii (BRepAlgoAPI_Check)",
                         "fail" if selfint else "pass", len(selfint), 0, ", ".join(selfint), selfint))
    checks.append(_check("brep.solid", "Geometria", "Każde ciało jest bryłą (Solid)", "fail" if not_solid else "pass",
                         len(not_solid), 0, ", ".join(not_solid), not_solid))

    # mesh topology of the printable tessellation
    leaky, nonman = [], {}
    for n, m in meshes.items():
        t = _mesh_topology(m)
        if not t["watertight"]:
            leaky.append(n)
        if t["non_manifold_edges"]:
            nonman[n] = t["non_manifold_edges"]
        meshes[n] = t["mesh"]
    checks.append(_check("mesh.watertight", "Szczelność", "Siatka eksportowa szczelna (watertight)", "fail" if leaky else "pass",
                         len(leaky), 0, ", ".join(leaky), leaky))
    checks.append(_check("mesh.non_manifold", "Szczelność", "Brak krawędzi non-manifold", "fail" if nonman else "pass",
                         sum(nonman.values()), 0, ", ".join(f"{k}:{v}" for k, v in nonman.items()), list(nonman)))

    # overlapping bodies (separate print parts must not interpenetrate)
    overlaps = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            try:
                va = eng.bodies[a]["shape"]
                vb = eng.bodies[b]["shape"]
                ba, bb = exact_bbox(va), exact_bbox(vb)
                if ba.xmax < bb.xmin or bb.xmax < ba.xmin or ba.ymax < bb.ymin or bb.ymax < ba.ymin or ba.zmax < bb.zmin or bb.zmax < ba.zmin:
                    continue
                vol = va.intersect(vb).Volume()
                if vol > 1e-3:
                    overlaps.append(f"{a}∩{b}={vol:.2f} mm³")
            except Exception:  # noqa: BLE001
                continue
    checks.append(_check("bodies.overlap", "Przecięcia", "Bryły nie przenikają się", "warn" if overlaps else "pass",
                         len(overlaps), 0, "; ".join(overlaps)))

    # dimensions vs user parameters
    bbs = [exact_bbox(eng.bodies[n]["shape"]) for n in names]
    lo = np.min([[b.xmin, b.ymin, b.zmin] for b in bbs], 0)
    hi = np.max([[b.xmax, b.ymax, b.zmax] for b in bbs], 0)
    size = (hi - lo).round(3).tolist()
    p = rt.state.get("params", {})
    exp = {k: p[k] for k in ("W", "D", "H") if isinstance(p.get(k), (int, float))}
    if exp:
        dev = []
        for k, idx in (("W", 0), ("D", 1), ("H", 2)):
            if k in exp:
                # embossed details may stick out on top; allow + emboss depth on Z
                tol = 0.05 + (float(p.get("logo_depth", 0)) if k == "H" else 0)
                if not (exp[k] - 0.05 <= size[idx] <= exp[k] + tol):
                    dev.append(f"{k}: {size[idx]} ≠ {exp[k]}")
        checks.append(_check("dims.params", "Wymiary", "Wymiary zgodne z parametrami W/D/H", "fail" if dev else "pass",
                             size, exp, "; ".join(dev)))
    else:
        checks.append(_check("dims.params", "Wymiary", "Wymiary zgodne z parametrami W/D/H", "unchecked", size, None,
                             "Brak parametrów W/D/H — nie ma z czym porównać"))
    checks.append(_check("dims.size", "Wymiary", "Gabaryt modelu [mm]", "info", size))

    # wall thickness (ray casting on the real tessellation)
    thin, measured = [], {}
    for n, m in meshes.items():
        if not m.is_watertight:
            continue
        t = _wall_thickness(m)
        if t is not None:
            measured[n] = round(t, 3)
            if t < MIN_WALL_FDM_MM - 1e-3:
                thin.append(n)
    if measured:
        expected_wall = p.get("wall")
        detail = ", ".join(f"{k}: {v} mm" for k, v in measured.items())
        st = "warn" if thin else "pass"
        checks.append(_check("print.wall_thickness", "Grubość ścianek", f"Min. grubość ścianki ≥ {MIN_WALL_FDM_MM} mm (FDM)",
                             st, min(measured.values()), MIN_WALL_FDM_MM, detail, thin))
        if isinstance(expected_wall, (int, float)) and "Base" in measured:
            ok = abs(measured["Base"] - expected_wall) <= max(0.1, expected_wall * 0.1)
            checks.append(_check("dims.wall_param", "Grubość ścianek", "Ścianka korpusu = parametr wall", "pass" if ok else "warn",
                                 measured["Base"], expected_wall))
    else:
        checks.append(_check("print.wall_thickness", "Grubość ścianek", "Grubość ścianek", "unchecked",
                             detail="Nie udało się zmierzyć (siatka nieszczelna lub zbyt mała)"))

    # printability
    fits = all(s <= b for s, b in zip(sorted(size), sorted(PRINT_BED_MM)))
    checks.append(_check("print.bed", "Druk 3D", "Mieści się na stole 256×256×256 mm", "pass" if fits else "fail", size, PRINT_BED_MM))
    checks.append(_check("print.overhangs", "Druk 3D", "Analiza nawisów / podpór", "unchecked",
                         detail="Nie zaimplementowano analizy nawisów"))

    # export round trip
    checks.append(_export_roundtrip(rt, "stl"))
    try:
        import cadquery as cq

        with tempfile.NamedTemporaryFile(suffix=".step") as tf:
            cq.exporters.export(eng.brep_compound(names), tf.name)
            back = cq.importers.importStep(tf.name).val()
            ok = back.isValid() and len(back.Solids()) >= len(names)
            checks.append(_check("export.step", "Eksport", "Eksport STEP (B-Rep) + ponowny import", "pass" if ok else "fail",
                                 len(back.Solids()), len(names)))
    except Exception as e:  # noqa: BLE001
        checks.append(_check("export.step", "Eksport", "Eksport STEP (B-Rep) wykonalny", "fail", detail=str(e)[:200]))
    return checks


# ============================================================================ GAME
def qa_game(rt, tri_budget: int = 150_000) -> list[dict]:
    from .engines.game.textures import PRESETS

    eng = rt.engine
    hidden = set(rt.state.get("hidden", []))
    parts = {n: p for n, p in eng.parts.items() if p["mesh"] is not None and n not in hidden}
    checks: list[dict] = []
    if not parts:
        return [_check("scene.empty", "Topologia", "Scena zawiera siatki", "fail", 0, ">0")]
    errs = rt.errors
    checks.append(_check("history.errors", "Topologia", "Operacje z historii wykonane bez błędu", "fail" if errs else "pass",
                         len(errs), 0, "; ".join(list(errs.values())[:3])))

    total = sum(len(p["mesh"].faces) for p in parts.values())
    checks.append(_check("poly.total", "Polygon count", f"Łączna liczba trójkątów ≤ {tri_budget:,}".replace(",", " "),
                         "pass" if total <= tri_budget else "warn", total, tri_budget))
    heavy = [n for n, p in parts.items() if len(p["mesh"].faces) > 20000]
    checks.append(_check("poly.per_object", "Polygon count", "Obiekty ≤ 20 000 trójkątów (limit MeshPart Roblox)",
                         "warn" if heavy else "pass", len(heavy), 0, ", ".join(heavy), heavy))

    degen, inverted, nonman = {}, [], {}
    for n, p in parts.items():
        t = _mesh_topology(p["mesh"])
        if t["degenerate_faces"]:
            degen[n] = t["degenerate_faces"]
        if t["non_manifold_edges"]:
            nonman[n] = t["non_manifold_edges"]
        if t["watertight"] and t["volume"] is not None and t["volume"] < 0:
            inverted.append(n)
    checks.append(_check("topo.degenerate", "Topologia", "Brak zdegenerowanych trójkątów", "warn" if degen else "pass",
                         sum(degen.values()), 0, ", ".join(f"{k}:{v}" for k, v in list(degen.items())[:8]), list(degen)))
    checks.append(_check("topo.non_manifold", "Topologia", "Brak krawędzi non-manifold", "warn" if nonman else "pass",
                         sum(nonman.values()), 0, ", ".join(f"{k}:{v}" for k, v in list(nonman.items())[:8]), list(nonman)))
    checks.append(_check("topo.normals", "Normal Maps", "Normalne skierowane na zewnątrz (zamknięte siatki)",
                         "fail" if inverted else "pass", len(inverted), 0, ", ".join(inverted), inverted))

    no_uv, bad_uv = [], []
    for n, p in parts.items():
        uv = getattr(p["mesh"].visual, "uv", None)
        if uv is None or len(uv) != len(p["mesh"].vertices):
            no_uv.append(n)
        elif not np.isfinite(uv).all():
            bad_uv.append(n)
    checks.append(_check("uv.present", "UV Mapping", "Każda siatka ma współrzędne UV", "fail" if no_uv else "pass",
                         len(no_uv), 0, ", ".join(no_uv), no_uv))
    checks.append(_check("uv.finite", "UV Mapping", "UV bez wartości NaN/Inf", "fail" if bad_uv else "pass", len(bad_uv), 0,
                         ", ".join(bad_uv), bad_uv))
    checks.append(_check("uv.overlap", "UV Mapping", "Brak nakładania wysp UV (lightmapy)", "unchecked",
                         detail="UV generowane projekcją z kafelkowaniem — nakładanie wysp nie jest analizowane"))

    no_mat = [n for n, p in parts.items() if not p.get("material") or p["material"].get("preset") not in PRESETS]
    checks.append(_check("pbr.materials", "Materiały PBR", "Każda siatka ma materiał PBR", "fail" if no_mat else "pass",
                         len(no_mat), 0, ", ".join(no_mat), no_mat))
    grey = [n for n, p in parts.items() if p.get("material", {}).get("color", "").lower() in ("#a4aab1", "#9aa3ad")]
    checks.append(_check("pbr.placeholder", "Materiały PBR", "Brak szarych materiałów zastępczych (clay)", "warn" if grey else "pass",
                         len(grey), 0, ", ".join(grey), grey))
    with_normal = sum(1 for p in parts.values() if PRESETS.get(p["material"]["preset"], {}).get("normal", 0) > 0)
    checks.append(_check("pbr.normal_maps", "Normal Maps", "Materiały z mapą normalnych", "info", with_normal, None,
                         f"{with_normal}/{len(parts)} siatek"))
    res = int(rt.state.get("texture_res", eng.texture_res))
    checks.append(_check("tex.resolution", "Tekstury", "Rozdzielczość tekstur ≥ 1024 px", "pass" if res >= 1024 else "warn", res, 1024))

    # scale & pivot
    ms = []
    for n in parts:
        m = eng._world_mesh(n)
        ms.append(m.bounds)
    lo = np.min([b[0] for b in ms], 0)
    hi = np.max([b[1] for b in ms], 0)
    size = (hi - lo).round(3).tolist()
    sane = 0.01 <= max(size) <= 500
    checks.append(_check("scale.size", "Skala", "Skala realistyczna (0.01–500 m)", "pass" if sane else "warn", size, "metry"))
    grounded = abs(lo[1]) <= 0.03
    checks.append(_check("pivot.ground", "Pivot", "Model stoi na ziemi (min Y ≈ 0)", "pass" if grounded else "warn",
                         round(float(lo[1]), 4), 0.0))
    roots = [n for n, p in eng.parts.items() if not p["parent"] and n not in hidden]
    centered = abs((lo[0] + hi[0]) / 2) < max(size) * 0.25 and abs((lo[2] + hi[2]) / 2) < max(size) * 0.25
    checks.append(_check("pivot.center", "Pivot", "Pivot sceny blisko środka modelu (XZ)", "pass" if centered else "warn",
                         [round(float((lo[0] + hi[0]) / 2), 3), round(float((lo[2] + hi[2]) / 2), 3)], [0, 0],
                         f"{len(roots)} obiektów głównych"))

    heavy_nolod = [n for n, p in parts.items() if len(p["mesh"].faces) > 2000 and not p["meta"].get("lods")]
    checks.append(_check("lod.present", "LOD", "LOD dla siatek > 2000 trójkątów", "warn" if heavy_nolod else "pass",
                         len(heavy_nolod), 0, ", ".join(heavy_nolod[:8]), heavy_nolod))
    checks.append(_export_roundtrip(rt, "glb"))
    return checks


def _export_roundtrip(rt, fmt: str) -> dict:
    """Export in memory with the real exporter path and re-import with trimesh."""
    try:
        scene = rt.engine.to_trimesh_scene(hidden=set(rt.state.get("hidden", [])), texture_res=256) if rt.mode == "game" \
            else rt.engine.to_trimesh_scene(hidden=set(rt.state.get("hidden", [])))
        n_src = sum(len(g.faces) for g in scene.geometry.values())
        if fmt == "glb":
            data = scene.export(file_type="glb", include_normals=True)
            back = trimesh.load(io.BytesIO(data), file_type="glb", force="scene")
        else:
            mesh = scene.to_mesh() if hasattr(scene, "to_mesh") else scene.dump(concatenate=True)
            data = mesh.export(file_type="stl")
            back = trimesh.load(io.BytesIO(data), file_type="stl", force="scene")
        n_back = sum(len(g.faces) for g in back.geometry.values())
        ok = n_back == n_src and n_back > 0
        return _check(f"export.{fmt}", "Eksport", f"Eksport {fmt.upper()} + ponowny import", "pass" if ok else "fail",
                      n_back, n_src, f"{len(data) / 1024:.0f} kB")
    except Exception as e:  # noqa: BLE001
        return _check(f"export.{fmt}", "Eksport", f"Eksport {fmt.upper()} + ponowny import", "fail", detail=str(e)[:200])


def run_qa(rt) -> dict[str, Any]:
    t0 = time.time()
    checks = qa_cad(rt) if rt.mode == "cad" else qa_game(rt)
    cats: dict[str, dict] = {}
    for c in checks:
        d = cats.setdefault(c["category"], {"pass": 0, "warn": 0, "fail": 0, "unchecked": 0, "info": 0, "scores": []})
        d[c["status"]] += 1
        if c["status"] in SCORE:
            d["scores"].append(SCORE[c["status"]])
    for d in cats.values():
        sc = d.pop("scores")
        d["score"] = round(sum(sc) / len(sc)) if sc else None  # None = nothing measured in this category
    measured = [SCORE[c["status"]] for c in checks if c["status"] in SCORE]
    return {"mode": rt.mode, "rev": rt.state["rev"], "checks": checks, "categories": cats,
            "score": round(sum(measured) / len(measured)) if measured else None,
            "failed": [c["id"] for c in checks if c["status"] == "fail"],
            "warnings": [c["id"] for c in checks if c["status"] == "warn"],
            "unchecked": [c["id"] for c in checks if c["status"] == "unchecked"],
            "ms": int((time.time() - t0) * 1000), "source": "technical-tests"}


def compact(report: dict) -> str:
    """Short text form for LLM context: only actionable lines."""
    lines = [f"Technical QA score {report['score']} (measured by code, rev {report['rev']}):"]
    for c in report["checks"]:
        if c["status"] in ("fail", "warn", "unchecked") or c["id"].startswith("dims"):
            lines.append(f"- [{c['status'].upper()}] {c['id']} {c['name']}: value={c['value']} expected={c['expected']} {c['detail']}".strip())
    return "\n".join(lines)
