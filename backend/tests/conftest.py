import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="ai3d-test-")
os.environ["DATA_DIR"] = _tmp
os.environ["LIVE_BUILD_STEP_DELAY"] = "0"
os.environ.pop("API_TOKEN", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402


@pytest.fixture(scope="session")
def logo_path() -> str:
    p = Path(_tmp) / "logo.png"
    im = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((20, 20, 280, 280), fill=(20, 90, 200, 255))
    d.ellipse((90, 90, 210, 210), fill=(0, 0, 0, 0))
    d.rectangle((300, 40, 380, 260), fill=(220, 40, 40, 255))
    im.save(p)
    return str(p)


@pytest.fixture()
def make_project(logo_path):
    import json

    from app import db, runtime

    def _make(mode: str):
        pid = db.new_id()
        with db.tx() as c:
            c.execute("INSERT INTO projects(id,name,mode,created,updated,state) VALUES(?,?,?,?,?,?)",
                      (pid, f"t-{mode}", mode, db.now(), db.now(), json.dumps(runtime.empty_state())))
            c.execute("INSERT INTO attachments(id,project_id,filename,mime,path) VALUES(?,?,?,?,?)",
                      ("logo" + pid, pid, "logo.png", "image/png", logo_path))
        return runtime.get_runtime(pid), [{"id": "logo" + pid, "mime": "image/png", "palette": ["#1f5fbf", "#d02828"]}]

    return _make


def run_plan(rt, plan):
    if plan.params:
        rt.set_params(plan.params)
    out = []
    for s in plan.steps:
        if s["tool"] == "__params":
            rt.set_params(s["args"]["updates"])
            continue
        out.append(rt.apply(s["tool"], s["args"], s["label"], "planner"))
    return out
