"""Universal export: STL, 3MF, OBJ(+MTL+textures), FBX, GLB, glTF, STEP, PLY, USDZ.
Every export is re-imported to validate it. Nothing is claimed that is not actually written."""
from __future__ import annotations

import io
import json
import math
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from .. import config
from ..engines.base import EngineError

UNIT_M = {"mm": 0.001, "cm": 0.01, "m": 1.0, "in": 0.0254}
FORMATS: dict[str, dict[str, Any]] = {
    "stl": {"label": "STL", "ext": ".stl", "units": "mm", "materials": False, "textures": False,
            "note": "Geometry only — STL stores no colours, materials or textures."},
    "3mf": {"label": "3MF", "ext": ".3mf", "units": "mm", "materials": True, "textures": False,
            "note": "Per-object colours (base materials). Opens in Bambu Studio / PrusaSlicer / Orca as separate colour parts."},
    "obj": {"label": "OBJ + MTL", "ext": ".zip", "units": "m", "materials": True, "textures": True,
            "note": "ZIP with .obj, .mtl and texture images (baseColor, normal, roughness, metallic)."},
    "fbx": {"label": "FBX", "ext": ".zip", "units": "cm", "materials": True, "textures": True,
            "note": "Binary FBX 7.x written by Assimp + texture images in a ZIP. Units cm (Unreal/Unity convention)."},
    "glb": {"label": "GLB", "ext": ".glb", "units": "m", "materials": True, "textures": True,
            "note": "Single file: geometry + PBR materials + embedded textures."},
    "gltf": {"label": "glTF", "ext": ".zip", "units": "m", "materials": True, "textures": True,
             "note": "ZIP with .gltf JSON, .bin buffer and textures."},
    "step": {"label": "STEP", "ext": ".step", "units": "mm", "materials": True, "textures": False, "cad_only": True,
             "note": "Exact B-Rep (AP214) with body names and colours. Only CAD projects have B-Rep geometry."},
    "ply": {"label": "PLY", "ext": ".ply", "units": "mm", "materials": True, "textures": False,
            "note": "Binary PLY with per-vertex colours."},
    "usdz": {"label": "USDZ", "ext": ".usdz", "units": "m", "materials": True, "textures": True,
             "note": "USD with UsdPreviewSurface materials (Apple AR Quick Look), written with Pixar usd-core."},
}

SUGGESTIONS = {
    "bambu": {"formats": ["3mf", "stl"], "why": "Bambu Studio keeps per-part colours from 3MF (AMS); STL is geometry-only."},
    "prusa": {"formats": ["3mf", "stl"], "why": "PrusaSlicer reads 3MF objects/colours; STL as fallback."},
    "blender": {"formats": ["glb", "fbx", "obj"], "why": "GLB keeps PBR materials best in Blender's importer."},
    "roblox": {"formats": ["fbx", "obj"], "why": "Roblox Studio 3D Importer accepts FBX/OBJ (≤20k tris per MeshPart). Run 'optimize for roblox' first."},
    "unity": {"formats": ["fbx", "gltf"], "why": "FBX natively; glTF via glTFast. LODs exported as *_LODn."},
    "unreal": {"formats": ["fbx", "glb"], "why": "FBX is the native interchange (cm units); glTF via Interchange."},
    "web": {"formats": ["glb"], "why": "GLB is the standard for three.js / model-viewer."},
    "cad": {"formats": ["step"], "why": "STEP keeps exact B-Rep surfaces for Fusion 360 / SolidWorks / FreeCAD."},
    "ar": {"formats": ["usdz", "glb"], "why": "USDZ for iOS Quick Look, GLB for Android Scene Viewer."},
}


def _axis_fix(src_mode: str, fmt: str) -> np.ndarray:
    """CAD is Z-up (mm), GAME is Y-up (m). Print/CAD formats want Z-up, DCC/web formats Y-up."""
    zup_formats = {"stl", "3mf", "ply", "step"}
    if src_mode == "cad" and fmt not in zup_formats:
        return trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0])
    if src_mode == "game" and fmt in zup_formats:
        return trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0])
    return np.eye(4)


def _scale(src_mode: str, units: str, scale: float) -> float:
    src = 0.001 if src_mode == "cad" else 1.0
    if units not in UNIT_M:
        raise EngineError(f"units must be one of {list(UNIT_M)}")
    return src / UNIT_M[units] * scale


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "model"


def _scene(rt, opts: dict, fmt: str) -> trimesh.Scene:
    eng = rt.engine
    kw = {"hidden": set(rt.state["hidden"]) if not opts.get("include_hidden") else set(),
          "texture_res": int(opts.get("texture_res", 1024)), "quality": opts.get("quality", "normal"),
          "only": opts.get("selection") or None}
    if rt.mode == "game":
        kw["include_lods"] = bool(opts.get("include_lods", True))
    scene = eng.to_trimesh_scene(**kw)
    if len(scene.geometry) == 0:
        raise EngineError("Nothing to export (scene empty or everything hidden / not selected).")
    T = _axis_fix(rt.mode, fmt)
    s = _scale(rt.mode, opts.get("units") or FORMATS[fmt]["units"], float(opts.get("scale", 1.0)))
    S = np.eye(4) * s
    S[3, 3] = 1
    scene.apply_transform(S @ T)
    return scene


def _strip_materials(scene: trimesh.Scene, keep_color: bool = True) -> None:
    for g in scene.geometry.values():
        if keep_color:
            col = _base_color(g)
            g.visual = trimesh.visual.ColorVisuals(g, face_colors=np.tile(col, (len(g.faces), 1)))
        else:
            g.visual = trimesh.visual.ColorVisuals(g)


def _base_color(g: trimesh.Trimesh) -> np.ndarray:
    mat = getattr(g.visual, "material", None)
    if mat is None:
        return np.array([200, 200, 200, 255], np.uint8)
    f = np.array(getattr(mat, "baseColorFactor", None) if getattr(mat, "baseColorFactor", None) is not None else [1, 1, 1, 1], float)
    if f.max() > 1.0:
        f = f / 255.0
    # glTF factors are linear; printers/3MF/PLY expect sRGB
    rgb = np.clip(f[:3], 0, 1)
    f = np.concatenate([np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * rgb ** (1 / 2.4) - 0.055), f[3:4]])
    tex = getattr(mat, "baseColorTexture", None)
    if tex is not None:
        arr = np.asarray(tex.convert("RGBA").resize((16, 16))).reshape(-1, 4) / 255.0
        f = f * arr.mean(0)
    return (np.clip(f, 0, 1) * 255).astype(np.uint8)


# ---------------------------------------------------------------- writers
def _write_stl(scene, out: Path, opts) -> None:
    mesh = scene.to_mesh() if hasattr(scene, "to_mesh") else scene.dump(concatenate=True)
    mesh.merge_vertices(merge_tex=True, merge_norm=True)
    mesh.export(str(out), file_type="stl")


def _write_ply(scene, out: Path, opts) -> None:
    _strip_materials(scene, keep_color=opts.get("materials", True))
    meshes = []
    for node in scene.graph.nodes_geometry:
        T, gname = scene.graph[node]
        g = scene.geometry[gname].copy()
        g.apply_transform(T)
        meshes.append(g)
    m = trimesh.util.concatenate(meshes)
    m.export(str(out), file_type="ply")


def _write_3mf(scene, out: Path, opts, units: str) -> None:
    import lib3mf

    w = lib3mf.get_wrapper()
    model = w.CreateModel()
    model.SetUnit({"mm": lib3mf.ModelUnit.MilliMeter, "cm": lib3mf.ModelUnit.CentiMeter, "m": lib3mf.ModelUnit.Meter,
                   "in": lib3mf.ModelUnit.Inch}[units])
    mg = model.AddBaseMaterialGroup()
    colors: dict[tuple, int] = {}
    for node in scene.graph.nodes_geometry:
        T, gname = scene.graph[node]
        g = scene.geometry[gname].copy()
        g.apply_transform(T)
        g.merge_vertices(merge_tex=True, merge_norm=True)
        col = tuple(int(c) for c in _base_color(scene.geometry[gname]))
        if opts.get("materials", True):
            if col not in colors:
                colors[col] = mg.AddMaterial(f"Color_{len(colors) + 1}", w.RGBAToColor(*col))
        mesh = model.AddMeshObject()
        mesh.SetName(str(node))
        mesh.SetGeometry([lib3mf.Position(tuple(map(float, v))) for v in g.vertices],
                         [lib3mf.Triangle(tuple(int(i) for i in f)) for f in g.faces])
        if opts.get("materials", True):
            mesh.SetObjectLevelProperty(mg.GetResourceID(), colors[col])
        model.AddBuildItem(mesh, w.GetIdentityTransform())
    model.QueryWriter("3mf").WriteToFile(str(out))


def _write_glb(scene, out: Path, opts) -> None:
    if not opts.get("materials", True):
        _strip_materials(scene, keep_color=False)
    elif not opts.get("textures", True):
        _strip_materials(scene, keep_color=True)
    out.write_bytes(scene.export(file_type="glb", include_normals=True))


def _write_gltf_dir(scene, d: Path, opts, name: str) -> Path:
    if not opts.get("materials", True):
        _strip_materials(scene, keep_color=False)
    elif not opts.get("textures", True):
        _strip_materials(scene, keep_color=True)
    files = trimesh.exchange.gltf.export_gltf(scene, include_normals=True)
    d.mkdir(parents=True, exist_ok=True)
    main = None
    for fn, data in files.items():
        target = d / fn
        if fn.endswith(".gltf"):
            target = d / f"{name}.gltf"
            main = target
        target.write_bytes(data)
    assert main is not None
    return main


def _write_obj_bundle(scene, d: Path, opts, name: str) -> Path:
    """Hand-written OBJ/MTL so that every material and texture map is referenced correctly."""
    d.mkdir(parents=True, exist_ok=True)
    obj = [f"# AI 3D Studio export\nmtllib {name}.mtl\n"]
    mtl = []
    mats: dict[int, str] = {}
    voff = vtoff = vnoff = 0
    for node in scene.graph.nodes_geometry:
        T, gname = scene.graph[node]
        g = scene.geometry[gname].copy()
        g.apply_transform(T)
        mat = getattr(g.visual, "material", None)
        mname = "default"
        if mat is not None and opts.get("materials", True):
            key = getattr(mat, "name", None) or id(mat)
            if key not in mats:
                mname = _safe(getattr(mat, "name", None) or f"mat{len(mats)}") + f"_{len(mats)}"
                mats[key] = mname
                col = _base_color(g) / 255.0
                lines = [f"newmtl {mname}", f"Kd {col[0]:.4f} {col[1]:.4f} {col[2]:.4f}", "Ka 0 0 0", "Ks 0.5 0.5 0.5",
                         f"Ns {200 * (1 - float(getattr(mat, 'roughnessFactor', 0.5) or 0.5)):.1f}", "illum 2",
                         f"d {col[3]:.3f}"]
                if opts.get("textures", True):
                    for attr, keyw in (("baseColorTexture", "map_Kd"), ("normalTexture", "norm"),
                                       ("metallicRoughnessTexture", "map_Pr")):
                        img = getattr(mat, attr, None)
                        if img is None:
                            continue
                        alpha = attr == "baseColorTexture" and img.mode == "RGBA"
                        fn = f"{mname}_{attr.replace('Texture', '')}.{'png' if alpha else 'jpg'}"
                        img.save(d / fn) if alpha else img.convert("RGB").save(d / fn, quality=92)
                        lines.append(f"{keyw} {fn}")
                        if keyw == "norm":
                            lines.append(f"map_Bump {fn}")
                mtl.append("\n".join(lines) + "\n")
            mname = mats[key]
        obj.append(f"o {_safe(str(node))}\nusemtl {mname}\n")
        v, n = g.vertices, g.vertex_normals
        obj.append("".join(f"v {a:.6f} {b:.6f} {c:.6f}\n" for a, b, c in v))
        uv = getattr(g.visual, "uv", None)
        has_uv = uv is not None and len(uv) == len(v)
        if has_uv:
            obj.append("".join(f"vt {a:.6f} {b:.6f}\n" for a, b in uv))
        obj.append("".join(f"vn {a:.5f} {b:.5f} {c:.5f}\n" for a, b, c in n))
        for f in g.faces + 1:
            if has_uv:
                obj.append("f " + " ".join(f"{i + voff}/{i + vtoff}/{i + vnoff}" for i in f) + "\n")
            else:
                obj.append("f " + " ".join(f"{i + voff}//{i + vnoff}" for i in f) + "\n")
        voff += len(v)
        vnoff += len(v)
        vtoff += len(v) if has_uv else 0
    (d / f"{name}.obj").write_text("".join(obj))
    (d / f"{name}.mtl").write_text("\n".join(mtl) or "newmtl default\nKd 0.8 0.8 0.8\n")
    return d / f"{name}.obj"


def _assimp(*args: str, timeout: int = 180) -> str:
    if not shutil.which(config.ASSIMP_BIN):
        raise EngineError("FBX export needs the 'assimp' CLI on the server (apt install assimp-utils).")
    r = subprocess.run([config.ASSIMP_BIN, *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise EngineError(f"assimp failed: {(r.stderr or r.stdout)[-400:]}")
    return r.stdout


def _write_fbx_bundle(scene, d: Path, opts, name: str) -> Path:
    src = d / "_src"
    gltf = _write_gltf_dir(scene, src, opts, name)
    # textures must sit next to the FBX with stable names
    out = d / f"{name}.fbx"
    _assimp("export", str(gltf), str(out), "-ffbx")
    for img in src.glob("*"):
        if img.suffix.lower() in (".png", ".jpg", ".jpeg"):
            shutil.copy(img, d / img.name)
    shutil.rmtree(src)
    return out


def _write_step(rt, out: Path, opts) -> None:
    if rt.mode != "cad":
        raise EngineError("STEP requires exact B-Rep geometry, which only CAD projects have. "
                          "Game assets are polygon meshes — use FBX/GLB/OBJ.")
    import cadquery as cq

    only = opts.get("selection") or None
    hidden = set(rt.state["hidden"]) if not opts.get("include_hidden") else set()
    names = [n for n in rt.engine.bodies if n not in hidden and (not only or n in only)]
    if not names:
        raise EngineError("Nothing to export.")
    s = _scale("cad", opts.get("units") or "mm", float(opts.get("scale", 1.0)))
    asm = cq.Assembly(name=_safe(rt.name))
    for n in names:
        b = rt.engine.bodies[n]
        shape = b["shape"].scale(s) if abs(s - 1) > 1e-12 else b["shape"]
        asm.add(shape, name=_safe(n), color=cq.Color(b["color"]) if opts.get("materials", True) else None)
    asm.export(str(out), "STEP")


def _write_usdz(scene, out: Path, opts, name: str) -> None:
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, UsdUtils, Vt

    tmp = Path(tempfile.mkdtemp(dir=config.EXPORT_DIR))
    usdc = tmp / f"{name}.usdc"
    stage = Usd.Stage.CreateNew(str(usdc))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/Root")
    stage.SetDefaultPrim(root.GetPrim())
    mats: dict = {}
    for i, node in enumerate(scene.graph.nodes_geometry):
        T, gname = scene.graph[node]
        g = scene.geometry[gname].copy()
        g.apply_transform(T)
        path = f"/Root/m{i}_{_safe(str(node))}"
        mesh = UsdGeom.Mesh.Define(stage, path)
        mesh.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(*map(float, p)) for p in g.vertices]))
        mesh.CreateFaceVertexCountsAttr(Vt.IntArray([3] * len(g.faces)))
        mesh.CreateFaceVertexIndicesAttr(Vt.IntArray([int(x) for x in g.faces.ravel()]))
        mesh.CreateNormalsAttr(Vt.Vec3fArray([Gf.Vec3f(*map(float, p)) for p in g.vertex_normals]))
        mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
        mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
        uv = getattr(g.visual, "uv", None)
        if uv is not None and len(uv) == len(g.vertices):
            pv = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex)
            pv.Set(Vt.Vec2fArray([Gf.Vec2f(float(a), float(b)) for a, b in uv]))
        mat = getattr(g.visual, "material", None)
        if mat is None or not opts.get("materials", True):
            continue
        key = getattr(mat, "name", None) or id(mat)
        if key not in mats:
            mpath = f"/Root/Materials/mat{len(mats)}"
            umat = UsdShade.Material.Define(stage, mpath)
            sh = UsdShade.Shader.Define(stage, mpath + "/PBR")
            sh.CreateIdAttr("UsdPreviewSurface")
            col = _base_color(g) / 255.0
            sh.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(float(getattr(mat, "roughnessFactor", 0.5) or 0.5) * 0.6)
            sh.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(min(1.0, float(getattr(mat, "metallicFactor", 0) or 0) * 0.6))
            sh.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(float(col[3]))
            tex = getattr(mat, "baseColorTexture", None)
            if tex is not None and opts.get("textures", True):
                fn = f"tex{len(mats)}.png"
                tex.convert("RGB").save(tmp / fn)
                reader = UsdShade.Shader.Define(stage, mpath + "/st")
                reader.CreateIdAttr("UsdPrimvarReader_float2")
                reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
                tx = UsdShade.Shader.Define(stage, mpath + "/diffuse")
                tx.CreateIdAttr("UsdUVTexture")
                tx.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("./" + fn))
                tx.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(reader.ConnectableAPI(), "result")
                tx.CreateInput("wrapS", Sdf.ValueTypeNames.Token).Set("repeat")
                tx.CreateInput("wrapT", Sdf.ValueTypeNames.Token).Set("repeat")
                tx.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
                sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(tx.ConnectableAPI(), "rgb")
            else:
                sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*map(float, col[:3])))
            umat.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
            mats[key] = umat
        UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(mats[key])
    stage.GetRootLayer().Save()
    ok = UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(str(usdc)), str(out))
    shutil.rmtree(tmp, ignore_errors=True)
    if not ok:
        raise EngineError("USDZ packaging failed")


# ---------------------------------------------------------------- validation (re-import)
def _bbox_report(lo, hi) -> dict:
    return {"size": [round(float(x), 4) for x in (np.asarray(hi) - np.asarray(lo))]}


def validate(fmt: str, path: Path, main: Path | None = None) -> dict[str, Any]:
    target = main or path
    try:
        if fmt in ("stl", "ply", "glb", "obj", "gltf"):
            loaded = trimesh.load(str(target), force="scene")
            tris = int(sum(len(g.faces) for g in loaded.geometry.values() if hasattr(g, "faces")))
            if tris == 0:
                return {"ok": False, "error": "Re-imported file has no triangles"}
            rep = {"ok": True, "reimported_with": "trimesh", "triangles": tris, "meshes": len(loaded.geometry),
                   **_bbox_report(*loaded.bounds)}
            if fmt in ("glb", "gltf", "obj"):
                mats = {id(getattr(g.visual, "material", None)) for g in loaded.geometry.values()}
                textured = sum(1 for g in loaded.geometry.values()
                               if getattr(getattr(g.visual, "material", None), "baseColorTexture", None) is not None
                               or getattr(getattr(g.visual, "material", None), "image", None) is not None)
                rep.update({"materials": len(mats), "textured_meshes": textured})
            if fmt == "stl":
                m = loaded.to_mesh() if hasattr(loaded, "to_mesh") else loaded.dump(concatenate=True)
                rep["watertight"] = bool(m.is_watertight)
            return rep
        if fmt == "3mf":
            import lib3mf

            w = lib3mf.get_wrapper()
            m = w.CreateModel()
            m.QueryReader("3mf").ReadFromFile(str(target))
            it = m.GetMeshObjects()
            n = tris = 0
            while it.MoveNext():
                o = it.GetCurrentMeshObject()
                n += 1
                tris += o.GetTriangleCount()
            mats = m.GetBaseMaterialGroups()
            ncol = 0
            while mats.MoveNext():
                ncol += mats.GetCurrentBaseMaterialGroup().GetCount()
            return {"ok": n > 0 and tris > 0, "reimported_with": "lib3mf", "objects": n, "triangles": tris, "colors": ncol}
        if fmt == "step":
            import cadquery as cq

            from ..engines.cad.engine import exact_bbox

            shp = cq.importers.importStep(str(target)).val()
            bb = exact_bbox(shp)
            return {"ok": shp.isValid(), "reimported_with": "OpenCascade", "solids": len(shp.Solids()),
                    "volume": round(shp.Volume(), 3), "size": [round(bb.xlen, 4), round(bb.ylen, 4), round(bb.zlen, 4)]}
        if fmt == "fbx":
            # re-import with assimp: convert back to OBJ and count real geometry (robust across assimp versions)
            with tempfile.TemporaryDirectory(dir=config.EXPORT_DIR) as td:
                back = Path(td) / "reimport.obj"
                _assimp("export", str(target), str(back), "-fobj")
                loaded = trimesh.load(str(back), force="scene")
                faces = int(sum(len(g.faces) for g in loaded.geometry.values() if hasattr(g, "faces")))
                mtl = back.with_suffix(".mtl")
                mats = mtl.read_text(errors="ignore").count("newmtl") if mtl.exists() else None
            return {"ok": faces > 0, "reimported_with": "assimp→obj", "meshes": len(loaded.geometry), "faces": faces,
                    "materials": mats}
        if fmt == "usdz":
            from pxr import Usd, UsdGeom

            st = Usd.Stage.Open(str(target))
            meshes = [p for p in st.Traverse() if p.IsA(UsdGeom.Mesh)]
            return {"ok": len(meshes) > 0, "reimported_with": "usd-core", "meshes": len(meshes)}
    except EngineError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"re-import failed: {e}"}
    return {"ok": False, "error": "no validator"}


# ---------------------------------------------------------------- entry
def export(rt, opts: dict[str, Any]) -> dict[str, Any]:
    fmt = (opts.get("format") or "").lower()
    if fmt not in FORMATS:
        raise EngineError(f"Unsupported format '{fmt}'. Supported: {', '.join(FORMATS)}")
    if opts.get("animation"):
        raise EngineError("This scene contains no animation tracks — nothing to export for 'animation'.")
    name = _safe(opts.get("filename") or rt.name)
    units = opts.get("units") or FORMATS[fmt]["units"]
    work = Path(tempfile.mkdtemp(prefix=f"{fmt}_", dir=config.EXPORT_DIR))
    main: Path | None = None
    if fmt == "step":
        out = work / f"{name}.step"
        _write_step(rt, out, opts)
    else:
        scene = _scene(rt, opts, fmt)
        if fmt == "stl":
            out = work / f"{name}.stl"
            _write_stl(scene, out, opts)
        elif fmt == "ply":
            out = work / f"{name}.ply"
            _write_ply(scene, out, opts)
        elif fmt == "3mf":
            out = work / f"{name}.3mf"
            _write_3mf(scene, out, opts, units)
        elif fmt == "glb":
            out = work / f"{name}.glb"
            _write_glb(scene, out, opts)
        elif fmt == "usdz":
            out = work / f"{name}.usdz"
            _write_usdz(scene, out, opts, name)
        else:
            bundle = work / name
            if fmt == "gltf":
                main = _write_gltf_dir(scene, bundle, opts, name)
            elif fmt == "obj":
                main = _write_obj_bundle(scene, bundle, opts, name)
            elif fmt == "fbx":
                main = _write_fbx_bundle(scene, bundle, opts, name)
            out = work / f"{name}_{fmt}.zip"
            level = zipfile.ZIP_DEFLATED if opts.get("compression", True) else zipfile.ZIP_STORED
            with zipfile.ZipFile(out, "w", level) as z:
                for f in sorted(bundle.rglob("*")):
                    if f.is_file():
                        z.write(f, f.relative_to(bundle))
    report = validate(fmt, out, main)
    report["units"] = units
    report["note"] = FORMATS[fmt]["note"]
    return {"path": str(out), "filename": out.name, "size": out.stat().st_size, "format": fmt, "validation": report,
            "token": out.relative_to(config.EXPORT_DIR).as_posix()}
