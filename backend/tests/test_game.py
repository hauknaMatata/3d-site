import json
import shutil
import struct

import pytest
from conftest import run_plan

from app.export.exporters import export
from app.planner.local import plan

CAR = "Stwórz realistyczny, kolorowy, futurystyczny samochód sportowy z czarnymi felgami, czerwonym lakierem i szczegółowym wnętrzem"


def _glb_json(path):
    b = open(path, "rb").read()
    n = struct.unpack("<I", b[12:16])[0]
    return json.loads(b[20:20 + n])


def test_car_plan_builds_colored_textured_model(make_project):
    rt, att = make_project("game")
    p = plan(CAR, "game", rt.snapshot(False), None, att)
    assert p.params["paint"].startswith("#c0") and p.params["rim"] == "#121214"
    stages = list(dict.fromkeys(s["stage"] for s in p.steps))
    for st in ("Główna geometria", "Karoseria", "Koła", "Szyby", "Wnętrze", "Nakładanie materiałów", "Generowanie tekstur", "Końcowa optymalizacja"):
        assert st in stages
    run_plan(rt, p)
    assert not rt.errors
    objs = {o["name"]: o for o in rt.engine.objects()}
    assert objs["Body"]["material"] == "car_paint" and objs["Body"]["color"] == p.params["paint"]
    assert objs["Glass"]["material"] == "glass"
    assert sum(1 for n in objs if n.startswith("Wheel") and objs[n]["kind"] == "group") == 4
    assert {"SeatBaseL", "SeatBackR", "Dashboard", "SteeringWheel"} <= set(objs)


def test_game_edits_rims_paint_roblox(make_project):
    rt, att = make_project("game")
    run_plan(rt, plan(CAR, "game", rt.snapshot(False), None, att))
    e = plan("Zmień felgi na srebrne", "game", rt.snapshot(False), None, att)
    rt.set_params(e.steps[0]["args"]["updates"])
    rim = next(o for o in rt.engine.objects() if o["name"] == "WheelFL_RimBarrel")
    assert rim["color"] == rt.state["params"]["rim"]
    e = plan("Zoptymalizuj model do Roblox", "game", rt.snapshot(False), None, att)
    r = rt.apply(e.steps[0]["tool"], e.steps[0]["args"])
    assert r["result"]["platform"] == "roblox"
    assert all(o.get("triangles", 0) <= 10000 for o in rt.engine.objects())


def test_glb_has_normals_materials_textures(make_project, tmp_path):
    rt, att = make_project("game")
    run_plan(rt, plan(CAR, "game", rt.snapshot(False), None, att))
    r = export(rt, {"format": "glb", "texture_res": 512})
    assert r["validation"]["ok"] and r["validation"]["textured_meshes"] > 20
    j = _glb_json(r["path"])
    assert all("NORMAL" in m["primitives"][0]["attributes"] for m in j["meshes"])
    assert len(j["materials"]) >= 10 and len(j["images"]) >= 10


@pytest.mark.skipif(not shutil.which("assimp"), reason="assimp CLI not installed")
def test_fbx_export(make_project):
    rt, att = make_project("game")
    rt.apply("add_wheel", {"name": "W"})
    r = export(rt, {"format": "fbx", "texture_res": 256})
    assert r["validation"]["ok"] and r["validation"]["meshes"] > 0, r["validation"]


def test_step_refused_for_mesh_projects(make_project):
    from app.engines.base import EngineError

    rt, _ = make_project("game")
    rt.apply("add_primitive", {"shape": "box"})
    with pytest.raises(EngineError):
        export(rt, {"format": "step"})


def test_decal_and_boolean(make_project):
    rt, att = make_project("game")
    rt.apply("add_primitive", {"name": "Box", "shape": "rounded_box", "size": [1, 1, 1], "radius": 0.1})
    rt.apply("add_primitive", {"name": "Cut", "shape": "sphere", "radius": 0.4, "position": [0.5, 0.5, 0]})
    rt.apply("boolean", {"target": "Box", "tool": "Cut"})
    d = rt.apply("apply_decal", {"target": "Box", "attachment_id": att[0]["id"], "side": "+z", "width": 0.5})
    assert d["result"]["triangles"] > 0
