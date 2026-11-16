from conftest import run_plan

from app.export.exporters import export
from app.planner.local import plan

PROMPT = "Stwórz kwadratową obudowę 100 × 100 × 30 mm z czterema otworami montażowymi i logo na górze"


def test_enclosure_geometry_dimensions_and_logo(make_project):
    rt, att = make_project("cad")
    p = plan(PROMPT, "cad", rt.snapshot(False), None, att)
    assert p.kind == "create" and p.params["W"] == 100 and p.params["H"] == 30
    run_plan(rt, p)
    assert not rt.errors
    names = {o["name"] for o in rt.engine.objects()}
    assert {"Base", "Lid"} <= names and any(n.startswith("Logo") for n in names)
    base = rt.engine.query("measure", {"target": "Base"})
    lid = rt.engine.query("measure", {"target": "Lid"})
    assert abs(base["size_mm"][0] - 100) < 0.05 and abs(base["size_mm"][1] - 100) < 0.05
    assert abs(lid["max"][2] - 30) < 0.05, "total height must be 30 mm"
    assert base["valid"] and lid["valid"]
    # 4 through holes with countersinks => lid volume clearly below a solid plate
    assert lid["volume_mm3"] < 100 * 100 * 2


def test_parametric_edit_height_and_color(make_project):
    rt, att = make_project("cad")
    run_plan(rt, plan(PROMPT, "cad", rt.snapshot(False), None, att))
    edit = plan("Zwiększ wysokość o 5 mm", "cad", rt.snapshot(False), None, att)
    assert edit.kind == "edit" and edit.steps[0]["tool"] == "__params"
    rt.set_params(edit.steps[0]["args"]["updates"])
    assert abs(rt.engine.query("measure", {"target": "Lid"})["max"][2] - 35) < 0.05
    col = plan("Zmień kolor na ciemny czerwony", "cad", rt.snapshot(False), None, att)
    rt.set_params(col.steps[0]["args"]["updates"])
    assert rt.engine.bodies["Base"]["color"] == rt.state["params"]["color"]


def test_undo_redo_and_invalid_edit_rollback(make_project):
    rt, _ = make_project("cad")
    rt.set_params({"W": 40})
    rt.apply("create_box", {"name": "B", "width": "=W", "depth": 20, "height": 10})
    rt.apply("fillet", {"target": "B", "radius": 2, "edges": "|Z"})
    rt.undo()
    assert len(rt.state["ops"]) == 1 and rt.state["redo"]
    rt.redo()
    assert len(rt.state["ops"]) == 2
    import pytest

    from app.engines.base import EngineError

    with pytest.raises(EngineError):
        rt.set_params({"W": 1})  # fillet r=2 cannot fit -> rejected and rolled back
    assert rt.state["params"]["W"] == 40 and not rt.errors


def test_exports_stl_3mf_step(make_project):
    rt, att = make_project("cad")
    run_plan(rt, plan(PROMPT, "cad", rt.snapshot(False), None, att))
    stl = export(rt, {"format": "stl"})
    assert stl["validation"]["ok"] and stl["validation"]["size"][0] == 100.0
    m3 = export(rt, {"format": "3mf"})
    assert m3["validation"]["ok"] and m3["validation"]["objects"] == 3 and m3["validation"]["colors"] >= 2
    st = export(rt, {"format": "step"})
    assert st["validation"]["ok"] and st["validation"]["solids"] >= 3
