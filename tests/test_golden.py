"""Hand-written goldens (bench/golden/<id>: plan.json + parts/*.py, no LLM) through every gate, the export and
D1/D2/D4 (harness/pipeline.py), then a GUI-style param edit on the saved assembly: B1-3 (T1.12), the mechanisms
pack gearbox (T2.8) and the 3-DOF arm at 200 mm / 50 g (T2.9)."""
from pathlib import Path

import numpy as np
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


# ---- mechanisms pack golden (T2.8): gear pair + mimic, hinged cover, snap latch on a fixed catch part ----------
MECH = ROOT / "bench" / "golden" / "m_gearbox"


@pytest.fixture(scope="module")
def mech(tmp_path_factory):
    return run_job(MECH, tmp_path_factory.mktemp("golden_mech"), robot="m_gearbox")


def test_mech_golden_passes_every_stage(mech):
    assert mech.ok, (mech.failed_stage, [(s.gate, s.cause) for s in mech.signals if s.level == "failure"])
    assert list(mech.stages) == ["plan", "part:base", "part:gear_a", "part:gear_b", "part:cover", "part:catch",
                                 "assembly", "print", "build", "export", "description"]
    assert [s for s in mech.signals if s.level == "warning"] == []


def test_mech_golden_facts(mech):
    a = mech.stages["assembly"]
    assert a["collisions"] == [] and a["n_poses"] == 81                # crank 9 x cover 9; the follower follows
    assert [t["ok"] for t in a["tests"]] == [True] * 4, a["tests"]
    gears = next(c for c in a["clearances"] if {c["a"], c["b"]} == {"gear_a", "gear_b"})
    assert 0 < gears["min_mm"] < 0.2                                    # teeth interleave over the whole crank turn
    assert a["tests"][2]["min_mm"] == pytest.approx(0.5, abs=0.01)      # cover vs base over j_cover (hinge gap)
    j = mech.stages["export"]["joints"]
    assert j["j_follower"]["type"] == "continuous" and j["j_cover"]["type"] == "revolute"
    urdf = Path(mech.urdf).read_text(encoding="utf-8")
    assert '<mimic joint="j_crank" multiplier="-0.4"' in urdf
    pp = mech.stages["print"]["parts"]
    assert all(p["watertight"] and p["manifold"] and p["fits"] for p in pp.values())
    assert {k: p["orientation"]["name"] for k, p in pp.items()} == {
        "base": "-z down", "gear_a": "-z down", "gear_b": "-z down", "cover": "+z down", "catch": "-x down"}


# ---- 3-DOF golden (T2.9): yaw on a roller turntable, shoulder + elbow U brackets on 623ZZ idler bearings --------
ARM3 = ROOT / "bench" / "golden" / "arm3"


@pytest.fixture(scope="module")
def arm3(tmp_path_factory):
    return run_job(ARM3, tmp_path_factory.mktemp("golden_arm3"), robot="arm3")


def test_arm3_golden_passes_every_stage(arm3):
    assert arm3.ok, (arm3.failed_stage, [(s.gate, s.cause) for s in arm3.signals if s.level == "failure"])
    assert list(arm3.stages) == ["plan", "part:base", "part:turret", "part:upper_arm", "part:forearm", "assembly",
                                 "print", "build", "export", "description"]
    # the two warnings: the shoulder / elbow servo cut-outs through their walls are ceilings to support
    assert sorted(s.location for s in arm3.signals if s.level == "warning") == ["parts/turret.py", "parts/upper_arm.py"]


def test_arm3_golden_facts(arm3):
    a = arm3.stages["assembly"]
    assert a["collisions"] == [] and a["n_poses"] == 126              # rest + 5 x 5 x 5 grid (cap 125)
    assert [t["ok"] for t in a["tests"]] == [True] * 9, a["tests"]
    assert a["tests"][3]["min_mm"] == pytest.approx(0) and a["tests"][4]["min_mm"] == pytest.approx(0)   # bearings seated
    assert a["tests"][5]["min_mm"] == pytest.approx(2.0, abs=0.01)  # upper arm vs turret lug over the shoulder
    assert a["tests"][6]["min_mm"] == pytest.approx(2.0, abs=0.01)  # forearm vs upper arm lug over the elbow
    assert a["tests"][7]["min_mm"] > 30                              # the bay sits behind, out of the +x sweep
    assert min(c["min_mm"] for c in a["clearances"] if c["min_mm"] is not None) >= 1.0
    assert a["reach_mm"] >= 200 and a["reach_mm"] == pytest.approx(205.25, abs=0.05)
    t = a["torques"]
    assert t["j_shoulder"]["max_abs_Nm"] < 0.5 * t["j_shoulder"]["allowed_Nm"]
    assert t["j_elbow"]["max_abs_Nm"] < 0.2 * t["j_elbow"]["allowed_Nm"]
    assert arm3.stages["export"]["joints"] == {
        "j_yaw": {"type": "revolute", "parent": "base", "child": "turret"},
        "j_shoulder": {"type": "revolute", "parent": "turret", "child": "upper_arm"},
        "j_elbow": {"type": "revolute", "parent": "upper_arm", "child": "forearm"}}
    pp = arm3.stages["print"]["parts"]
    assert all(p["watertight"] and p["manifold"] and p["fits"] for p in pp.values())
    assert {k: p["orientation"]["name"] for k, p in pp.items()} == {
        "base": "-z down", "turret": "-z down", "upper_arm": "-y down", "forearm": "-y down"}
    assert all(p["orientation"]["support_mm2"] <= 0.15 * p["orientation"]["footprint_mm2"] for p in pp.values())
    assert max(p["extents_mm"][0] for p in pp.values()) <= 180         # generic_fdm build volume
    assert arm3.stages["description"]["results"]["D2"]["gravity_max_err_Nm"] < 1e-6


def test_arm3_assembly_follows_param_edits(arm3, tmp_path):
    """Plan param upper_len (moves the elbow servo, its bearing and the forearm) and part param turret sh_h. The WB
    solver keeps free joints near their old placements, so the joints may turn a little after the edit: check the
    shoulder axis height and the link lengths, not the tool's absolute pose."""
    script = ROOT / "tests" / "freecad" / "golden_edit.py"
    r = run_request({"op": "run_script", "script": str(script), "fcstd": arm3.assembly, "tool_owner": "forearm",
                     "edits": [["upper_arm", "upper_len", "=120 mm"], ["turret", "sh_h", "=45 mm"]],
                     "probes": [["upper_arm", "horn_seat"], ["forearm", "horn_seat"]]}, tmp_path / "io")
    assert r.ok, [s.cause for s in r.signals]
    out = r.result
    assert out["solve"] == 0 and out["solve_after_edit"] == 0
    assert out["invalid"] == [] and out["solids"] == {"upper_arm": 1, "turret": 1}
    # saved rest pose. x: upper_len + fore_len (shoulder axis over the yaw axis); y: shoulder horn face 12 + 15.9,
    # elbow horn face el_z + 15.9 = 3.9, forearm tool on the bracket mid plane -21.95; z: yaw horn 29.3 + 15.9,
    # sh_h 40, beam offset 9
    assert out["tool"] == pytest.approx([205, 27.9 + 3.9 - 21.95, 45.2 + 40 + 9], abs=1e-6)
    shoulder, elbow = (np.array(v) for v in out["probes"])
    tool = np.array(out["tool_after_edit"])
    assert shoulder[2] == pytest.approx(45.2 + 45, abs=1e-6)                         # on the shoulder axis: sh_h
    assert np.linalg.norm(elbow - shoulder) == pytest.approx(np.hypot(120, 3.9), abs=1e-6)   # upper_len
    assert np.linalg.norm(tool - elbow) == pytest.approx(np.linalg.norm([105, 9, 21.95]), abs=1e-6)
