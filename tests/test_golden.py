"""T1.12: hand-written golden B1-3 (bench/golden/b1_3: plan.json + parts/*.py, no LLM) through every P1 gate,
the export and D1/D2/D4 (harness/pipeline.py), then a GUI-style param edit on the saved assembly."""
from pathlib import Path

import pytest

from harness.pipeline import run_job
from harness.run import run_request

ROOT = Path(__file__).resolve().parents[1]
JOB = ROOT / "bench" / "golden" / "b1_3"


@pytest.fixture(scope="module")
def job(tmp_path_factory):
    return run_job(JOB, tmp_path_factory.mktemp("golden"), robot="b1_3")


def test_golden_passes_every_stage(job):
    assert job.ok, (job.failed_stage, [(s.gate, s.cause) for s in job.signals if s.level == "failure"])
    assert list(job.stages) == ["plan", "part:base", "part:turret", "part:arm", "assembly", "print", "build",
                                "export", "description"]
    # the one warning: the shoulder servo's 40 mm cut-out through the turret wall is a ceiling to support
    assert [(s.location, s.cause.split(" needs")[0]) for s in job.signals if s.level == "warning"] ==         [("parts/turret.py", "turret")]


def test_golden_facts(job):
    a = job.stages["assembly"]
    assert a["collisions"] == [] and a["n_poses"] == 82            # rest + 9 x 9 (rest is off the shoulder grid)
    assert [t["ok"] for t in a["tests"]] == [True] * 5, a["tests"]
    assert a["tests"][2]["min_mm"] == pytest.approx(2.0, abs=0.01)  # arm vs turret over the shoulder range
    assert min(c["min_mm"] for c in a["clearances"] if c["min_mm"] is not None) > 1.0
    assert a["reach_mm"] >= 150
    assert a["torques"]["j_shoulder"]["max_abs_Nm"] < 0.5 * a["torques"]["j_shoulder"]["allowed_Nm"]
    assert job.stages["export"]["joints"] == {"j_yaw": {"type": "revolute", "parent": "base", "child": "turret"},
                                              "j_shoulder": {"type": "revolute", "parent": "turret", "child": "arm"}}
    pp = job.stages["print"]["parts"]
    assert all(p["watertight"] and p["manifold"] and p["fits"] for p in pp.values())
    # L7 orientation: plates flat, the arm on its side (beam flush with the bracket edge)
    assert {k: p["orientation"]["name"] for k, p in pp.items()} == {"base": "-z down", "turret": "-z down", "arm": "-y down"}
    assert all(p["orientation"]["support_mm2"] <= 0.15 * p["orientation"]["footprint_mm2"] for p in pp.values())
    assert (Path(job.stages["print"]["report"])).is_file()
    assert job.stages["description"]["results"]["D2"]["gravity_max_err_Nm"] < 1e-6


def test_golden_assembly_follows_param_edits(job, tmp_path):
    script = ROOT / "tests" / "freecad" / "golden_edit.py"
    r = run_request({"op": "run_script", "script": str(script), "fcstd": job.assembly}, tmp_path / "io")
    assert r.ok, [s.cause for s in r.signals]
    out = r.result
    assert out["solve"] == 0 and out["solve_after_edit"] == 0
    assert out["invalid"] == [] and out["arm_solids"] == 1
    # yaw horn at z 36 + 15.9; shoulder horn face at y 12 + 15.9, z sh_h; tool at arm_len, U-bracket mid plane,
    # 9 mm toward the beam's flush edge (arm -y = world +z here)
    assert out["tool"] == pytest.approx([155, 27.9 - 21.95, 51.9 + 40 + 9], abs=1e-6)
    assert out["tool_after_edit"] == pytest.approx([180, 27.9 - 21.95, 51.9 + 45 + 9], abs=1e-6)
