"""T1.7: pose grid, static loads, plan validator (pure Python) and the L5/L6/L7 gates through the worker."""
import json
import math
from pathlib import Path

import numpy as np
import pytest
import trimesh

from assembly import kinematics as K
from harness.tools import compile_part
from profiles import load_profile
from schemas.plan import AssemblyPlan
from validate.assembly_check import check_assembly
from validate.masses import printed_mass_kg
from validate.plan_check import check_plan
from validate.poses import grid, joint_sweep, sweep_poses
from validate.run_tests import evaluate, probe_requests
from agents.router import route
from harness.pipeline import JobReport
from validate.orient import best_orientation, rect_fits
from validate.printability import check_holes, check_mesh, check_print, fit_table
from validate.statics import joint_loads


def conn(name, o=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="pivot"):
    return {"name": name, "o": list(o), "z": list(z), "x": list(x), "label": label}


def arm_plan(arm_len=150.0, payload_g=50, reach=150, **over):
    """Yaw + shoulder (B1-3 shape): base -> s_yaw -> turret -> s_sh (axis horizontal) -> arm with tool."""
    plan = {
        "ground": "base", "dof": 2,
        "requirements": {"pack": "manipulator", "dof": 2, "reach_mm": reach, "payload_g": payload_g},
        "components": [{"id": "s_yaw", "card": "servo/mg996r", "attach_to": "base.servo_mount"},
                       {"id": "s_sh", "card": "servo/mg996r", "attach_to": "turret.servo_mount"}],
        "parts": [
            {"name": "base", "intent": "plate", "connectors": [conn("servo_mount", (0, 0, 10), label="servo_mount")]},
            {"name": "turret", "intent": "bracket", "connectors": [
                conn("horn_seat", label="servo_horn"), conn("servo_mount", (0, 0, 40), z=(0, 1, 0), label="servo_mount")]},
            {"name": "arm", "intent": "link", "connectors": [
                conn("horn_seat", label="servo_horn"), conn("tip", (arm_len, 0, 0), label="tool")]},
        ],
        "joints": [
            {"name": "j_yaw", "type": "revolute", "parent": "s_yaw.horn", "child": "turret.horn_seat", "driver": "s_yaw",
             "limits": {"lower": -1.57, "upper": 1.57}},
            {"name": "j_sh", "type": "revolute", "parent": "s_sh.horn", "child": "arm.horn_seat", "driver": "s_sh",
             "limits": {"lower": -1.57, "upper": 1.57}},
        ],
    }
    plan.update(over)
    return AssemblyPlan.model_validate(plan)


# ---- pure Python ----------------------------------------------------------------------------
def test_grid_decimates_and_starts_at_rest():
    p = arm_plan().model_dump()
    poses = grid(p, 9, 25)
    assert poses[0] == {"j_yaw": 0.0, "j_sh": 0.0}
    assert len(poses) == 25 and {round(q["j_yaw"], 4) for q in poses[1:]} == {-1.57, -0.785, 0.0, 0.785, 1.57}
    p["joints"][1]["limits"] = {"lower": 0.2, "upper": 1.0, "effort": 1, "velocity": 1}
    assert grid(p, 9, 25)[0] == {"j_yaw": 0.0, "j_sh": 0.2}  # rest clamped into the limits
    p["joints"][1]["mimic"] = {"joint": "j_yaw"}
    assert len(grid(p, 9, 25)) == 9                         # coupled joints are not sampled


def _chain(n):
    """n revolute joints, +-1 rad, as a plan dict (only what the samplers read)."""
    return {"joints": [{"name": "j%d" % i, "type": "revolute", "limits": {"lower": -1.0, "upper": 1.0}} for i in range(n)]}


def test_sweep_poses_grid_then_latin_hypercube():
    assert len(sweep_poses(_chain(1))) == 9
    assert len(sweep_poses(_chain(2))) == 81                    # 9 x 9, rest (0, 0) is a grid point
    three = sweep_poses(_chain(3))
    assert len(three) == 125 and {q["j0"] for q in three} == {-1.0, -0.5, 0.0, 0.5, 1.0}   # 5 per joint
    four = sweep_poses(_chain(4))
    assert len(four) == 125 and four[0] == {f"j{i}": 0.0 for i in range(4)}
    assert {"j0": -1.0, "j1": 0.0, "j2": 0.0, "j3": 0.0} in four and {"j0": 0.0, "j1": 0.0, "j2": 0.0, "j3": 1.0} in four
    lhs = four[1 + 2 * 4:]
    for j in ("j0", "j3"):                                      # one sample per stratum and joint
        strata = sorted(int((q[j] + 1.0) / 2.0 * len(lhs)) for q in lhs)
        assert strata == list(range(len(lhs)))
    assert sweep_poses(_chain(4)) == four                       # seeded: reproducible
    assert [q["j1"] for q in joint_sweep(_chain(2), "j1", 3)] == [-1.0, 0.0, 1.0]


def test_plan_tests_schema_and_refs():
    plan = arm_plan(tests=[
        {"expect": "contact", "a": "s_sh", "b": "turret", "near": "turret.servo_mount"},
        {"expect": "clearance", "a": "arm", "b": "turret", "over_joint": "j_sh"},
        {"expect": "reach", "value_mm": 140},
        {"expect": "allow_overlap", "a": "arm", "b": "s_sh", "reason": "horn spline in its recess"}])
    assert plan.overlap_pairs() == [("arm", "s_sh")]
    assert check_plan(plan).ok
    with pytest.raises(ValueError):
        arm_plan(tests=[{"expect": "clearance", "a": "arm.tip", "b": "turret"}])          # owner, not connector
    with pytest.raises(ValueError):
        arm_plan(tests=[{"expect": "allow_overlap", "a": "arm", "b": "s_sh"}])            # reason required
    bad = arm_plan(tests=[{"expect": "clearance", "a": "arm", "b": "tower", "over_joint": "j_x"},
                          {"expect": "contact", "a": "s_sh", "b": "turret", "near": "turret.nope"}],
                   allow_overlap=[("arm", "ghost")])
    causes = " | ".join(s.cause for s in check_plan(bad).signals if s.level == "failure")
    assert "unknown owner 'tower'" in causes and "over_joint 'j_x'" in causes
    assert "no connector 'turret.nope'" in causes and "unknown owner 'ghost'" in causes


def test_run_tests_evaluate_and_route():
    plan = arm_plan(tests=[
        {"expect": "contact", "a": "s_sh", "b": "turret", "tol_mm": 0.5},
        {"expect": "clearance", "a": "arm", "b": "turret", "min_mm": 1.0, "over_joint": "j_sh", "samples": 5},
        {"expect": "clearance", "a": "arm", "b": "base", "min_mm": 2.0},
        {"expect": "reach", "op": ">=", "value_mm": 160}])
    sweep = grid(plan.model_dump(), 3, 9)
    probes, index = probe_requests(plan, sweep)
    assert index == {0: 0, 1: 1, 2: 2} and len(probes[0]["poses"]) == 1
    assert len(probes[1]["poses"]) == 5 and probes[2]["poses"] == sweep
    measured = [{"min_mm": 0.0, "q": {}, "points": None},
                {"min_mm": 0.4, "q": {"j_sh": 1.57}, "points": [[0, 0, 0], [0, 0, 0.4]]},
                {"min_mm": 5.0, "q": {}, "points": None}]
    res, sigs = evaluate(plan, measured, index, reach_fn=lambda tool: (150.0, {}))
    assert [r["ok"] for r in res] == [True, False, True, False]
    assert [s.location for s in sigs] == ["arm <-> turret", "plan.tests[3]"]
    r = route(JobReport(ok=False, failed_stage="assembly", signals=sigs[:1]), plan)
    assert r.kind == "code" and list(r.parts) == ["arm", "turret"]


def test_joint_loads_point_mass():
    """0.1 kg 100 mm out on a horizontal (+y) axis: 0.0981 N.m; pointing down after +90 deg: 0."""
    plan = {"ground": "base", "parts": [{"name": "base"}, {"name": "link"}],
            "joints": [{"name": "j", "type": "revolute", "parent": "base.a", "child": "link.b"}]}
    frames = {"base": {"a": K.frame((0, 0, 50), (0, 1, 0), (1, 0, 0))}, "link": {"b": K.identity()}}
    tree = K.Tree(plan, frames)
    assert tree.ok, tree.signals
    masses = [("link", 0.1, [100.0, 0.0, 0.0])]
    # link x = joint x = world +x; mass at +x, axis +y: r x F = (0.1, 0, 0) x (0, 0, -0.981) = (0, 0.0981, 0)
    assert joint_loads(tree, {"j": 0.0}, masses)["j"] == pytest.approx(0.1 * 9.81 * 0.1)
    assert joint_loads(tree, {"j": math.pi / 2}, masses)["j"] == pytest.approx(0.0, abs=1e-12)


def test_plan_check_passes_and_estimates():
    r = check_plan(arm_plan())
    assert r.ok, [s.cause for s in r.signals]
    assert r.dof == 2 and r.tool == "arm.tip"
    assert r.reach_mm > 150
    # shoulder at full horizontal extension: payload 50 g at 150 mm + rod 75 g at 75 mm (+ nothing else downstream)
    assert r.torques["j_sh"]["max_abs_Nm"] == pytest.approx(9.81 * (0.05 * 0.15 + 0.075 * 0.075), rel=1e-6)
    assert r.torques["j_yaw"]["max_abs_Nm"] == pytest.approx(0.0, abs=1e-9)
    assert r.torques["j_sh"]["allowed_Nm"] == pytest.approx(0.54)


@pytest.mark.parametrize("plan, needle, gate", [
    (arm_plan(arm_len=600, payload_g=100), "static torque", "L6"),
    (arm_plan(reach=400), "reach", "L5"),
    (arm_plan(dof=3), "plan.dof = 3 but the plan has 2", "plan"),
    (arm_plan(requirements={"dof": 3, "pack": "manipulator"}), "the spec asks for 3", "plan"),
    (arm_plan(components=[{"id": "s_yaw", "card": "servo/nope", "attach_to": "base.servo_mount"},
                          {"id": "s_sh", "card": "servo/mg996r", "attach_to": "turret.servo_mount"}]), "no card", "plan"),
])
def test_plan_check_failures(plan, needle, gate):
    r = check_plan(plan)
    assert not r.ok
    assert any(needle in s.cause and s.gate == gate and s.level == "failure" for s in r.signals), \
        [(s.gate, s.cause) for s in r.signals]


def test_plan_check_driver_and_tool_rules():
    p = arm_plan().model_dump()
    p["joints"][1]["driver"] = "s_yaw"                       # one servo, two joints
    p["joints"][0]["limits"] = {"lower": -2.0, "upper": 2.0}  # beyond the MG996R +-90 deg
    p["parts"][2]["connectors"][1]["label"] = "pivot"         # no tool
    causes = " | ".join(s.cause for s in check_plan(AssemblyPlan.model_validate(p)).signals)
    assert "drives both" in causes and "exceed servo/mg996r travel" in causes and "labelled 'tool'" in causes
    p = arm_plan().model_dump()
    p["joints"][1]["parent"] = "turret.servo_mount"          # joint off the servo it names as driver
    r = check_plan(AssemblyPlan.model_validate(p))
    assert any("neither side is on that servo" in s.cause for s in r.signals)


def test_printed_mass_model():
    prof = load_profile()
    # 20 mm cube: V 8000, A 2400, shell 2400 * 1.35 = 3240; m = 1.27e-6 * (3240 + 0.2 * 4760)
    assert printed_mass_kg(8000, 2400, "PETG", prof) == pytest.approx(1.27e-6 * (3240 + 0.2 * 4760))


def test_check_mesh_rules():
    prof = load_profile()
    info, sigs = check_mesh("ok", trimesh.creation.box((40, 30, 10)), prof)
    assert sigs == [] and info["fits"] and info["min_wall_mm"] == pytest.approx(10, abs=1e-3)
    _, sigs = check_mesh("big", trimesh.creation.box((260, 20, 20)), prof)
    assert [s.cause for s in sigs if "build volume" in s.cause]
    info, sigs = check_mesh("diag_ok", trimesh.creation.box((190, 20, 10)), prof)   # corner to corner on the bed
    assert sigs == [] and info["fits"]
    _, sigs = check_mesh("thin", trimesh.creation.box((40, 30, 0.8)), prof)
    assert any("walls down to 0.80" in s.cause for s in sigs)
    open_box = trimesh.creation.box((10, 10, 10))
    open_box.update_faces(list(range(len(open_box.faces) - 2)))
    _, sigs = check_mesh("open", open_box, prof)
    assert any("not a closed manifold solid" in s.cause for s in sigs)


def _table():
    """60 x 60 x 5 slab on a 10 x 10 x 40 post (slab on top in the part frame)."""
    slab = trimesh.creation.box((60, 60, 5))
    slab.apply_translation((0, 0, 42.5))
    post = trimesh.creation.box((10, 10, 40))
    post.apply_translation((0, 0, 20))
    return trimesh.boolean.union([slab, post])


def test_orientation_and_overhang():
    prof = load_profile()
    assert rect_fits(20, 190, 180, 180) and not rect_fits(20, 260, 180, 180)
    b = best_orientation(_table(), prof, [])
    assert b["name"] == "+z down" and b["support_mm2"] == 0 and b["contact_mm2"] == pytest.approx(3600)
    rows = {c["name"]: c for c in b["candidates"]}
    # upright: the slab underside is anchored only within max_bridge / 2 of the post (a 20 x 20 square)
    assert rows["-z down"]["support_mm2"] == pytest.approx(3600 - 100 - 400, rel=0.05)
    # on its side the post is a 40 x 10 cantilever: 5 mm of it reach from the slab
    assert rows["-x down"]["support_mm2"] == pytest.approx(350, abs=1)
    # a 6 mm horizontal hole bridges; a 30 x 30 pocket ceiling keeps a 20 x 20 centre unsupported
    hole = trimesh.creation.cylinder(radius=3, height=40, sections=64)
    hole.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, (0, 1, 0)))
    holed = trimesh.boolean.difference([trimesh.creation.box((30, 20, 20)), hole])
    pocket = trimesh.creation.box((30, 30, 10))
    pocket.apply_translation((0, 0, -5))
    cup = trimesh.boolean.difference([trimesh.creation.box((40, 40, 20)), pocket])
    rows = {c["name"]: c for c in best_orientation(holed, prof, [])["candidates"]}
    assert rows["-z down"]["support_mm2"] == 0
    rows = {c["name"]: c for c in best_orientation(cup, prof, [])["candidates"]}
    assert rows["-z down"]["support_mm2"] == pytest.approx(400, abs=1)
    # a load path along x keeps a long bar flat; along z it would cost LOAD_LAMBDA_MM per mm
    bar = trimesh.creation.box((150, 20, 6))
    assert best_orientation(bar, prof, [("a", (-70, 0, 0)), ("b", (70, 0, 0))])["name"] in ("-z down", "+z down")


def test_check_mesh_support_cap():
    prof = load_profile()
    _, sigs = check_mesh("table", _table(), prof)                          # prints slab down: no support
    assert sigs == []
    # 3-D cross of 60 x 10 x 10 bars: in every orientation the four level arms are 25 mm cantilevers
    bars = [trimesh.creation.box(e) for e in ((60, 10, 10), (10, 60, 10), (10, 10, 60))]
    info, sigs = check_mesh("jack", trimesh.boolean.union(bars), prof)
    o = info["orientation"]
    assert o["support_mm2"] == pytest.approx(4 * 20 * 10, abs=5)          # 5 mm of each arm reach the hub
    assert [s.level for s in sigs] == ["failure"] and "of support" in sigs[0].cause
    assert sigs[0].evidence["cap_mm2"] == pytest.approx(0.15 * o["footprint_mm2"], abs=0.01)


def _golden_plan():
    return json.loads((Path(__file__).resolve().parents[1] / "bench/golden/b1_3/plan.json").read_text(encoding="utf-8"))


def test_fit_probe():
    prof = load_profile()
    table = fit_table(None, prof)
    # M2 2.0 = tap_m2 + horizontal extra, M4 4.0 = the insert_m3 hole: both are valid printed holes
    assert table["bare"] == {2.5: "M2.5", 3.0: "M3", 5.0: "M5"}
    seat = fit_table(AssemblyPlan.model_validate({**_golden_plan(), "components": [
        {"id": "b1", "card": "bearing/608zz", "attach_to": "base.servo_mount"}]}), prof)
    assert seat["bare"][22.0].startswith("b1 seat") and "press b1 seat (bearing/608zz outer_d)" in seat["valid"][22.2]
    up = {"axis": [0, 0, 1], "center": [0, 0, 0], "length": 5}
    side = {"axis": [1, 0, 0], "center": [0, 0, 5], "length": 20}
    sigs = check_holes("p", [{**up, "d": 3.0}, {**up, "d": 3.4}, {**up, "d": 2.7}], np.eye(3), table)
    assert [s.level for s in sigs] == ["failure"] and "bare M3 nominal" in sigs[0].cause
    # horizontal on the bed: a clearance fit sags (warning); a tap fit or one with the extra does not
    sigs = check_holes("p", [{**side, "d": 3.4}, {**side, "d": 2.7}, {**side, "d": 3.6 + 0.2}], np.eye(3), table)
    assert [s.level for s in sigs] == ["warning"] and sigs[0].evidence["holes"] == [{**side, "d": 3.4}]


# ---- worker: interference, reach and printed-mass torque on real parts --------------------------
BASE = '''from agenticcad import Part


def build(P):
    part = Part("base")
    p = part.params(side=120, thick=10)
    part.pad(part.sketch("plate", on="XY").rect(p.side, p.side), p.thick)
    part.connector("servo_mount", origin=(0, 0, p.thick), z=(0, 0, 1), x=(1, 0, 0), label="servo_mount")
    {mount}
    return part
'''

ARM = '''from agenticcad import Part


def build(P):
    part = Part("arm")
    p = part.params(length=100, width=30, thick=6)
    part.pad(part.sketch("profile", on="XY").slot((0, 0), (p.length, 0), p.width), p.thick)
    part.connector("horn_seat", origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="servo_horn")
    part.connector("tip", origin=(p.length, 0, p.thick), z=(0, 0, 1), x=(1, 0, 0), label="tool")
    {mate}
    return part
'''


def yaw_plan():
    return AssemblyPlan.model_validate({
        "ground": "base", "dof": 1, "requirements": {"dof": 1, "reach_mm": 90, "payload_g": 50},
        "components": [{"id": "s_yaw", "card": "servo/mg996r", "attach_to": "base.servo_mount"}],
        "parts": [
            {"name": "base", "intent": "plate", "connectors": [conn("servo_mount", (0, 0, 10), label="servo_mount")]},
            {"name": "arm", "intent": "bar", "connectors": [conn("horn_seat", label="servo_horn"),
                                                            conn("tip", (100, 0, 6), label="tool")]},
        ],
        "joints": [{"name": "j_yaw", "type": "revolute", "parent": "s_yaw.horn", "child": "arm.horn_seat",
                    "driver": "s_yaw", "limits": {"lower": -1.57, "upper": 1.57}}],
    })


def _compile(tmp, tag, plan, mount, mate):
    files = {}
    for name, src in (("base", BASE.format(mount=mount)), ("arm", ARM.format(mate=mate))):
        prog = tmp / tag / f"{name}.py"
        prog.parent.mkdir(parents=True, exist_ok=True)
        prog.write_text(src, encoding="utf-8")
        r = compile_part(prog, tmp / "io", out=tmp / tag / f"{name}.FCStd", plan=plan)
        assert r.ok, r.feedback
        files[name] = r.fcstd
    return files


@pytest.fixture(scope="module")
def parts(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("val")
    plan = yaw_plan()
    good = _compile(tmp, "good", plan, 'part.mount("s_yaw", at="servo_mount")', 'part.mate_to("s_yaw.horn", at="horn_seat")')
    bad = _compile(tmp, "bad", plan, "", "")
    return tmp, plan, good, bad


def test_check_assembly_clean(parts):
    tmp, plan, good, _ = parts
    r = check_assembly(plan, good, tmp / "io")
    assert r.ok, r.feedback
    assert r.collisions == [] and r.n_poses == 9
    # moving pairs only (base-s_yaw are one rigid group); the arm sits on the horn face over the servo
    assert {(c["a"], c["b"]) for c in r.clearances} == {("arm", "base"), ("arm", "s_yaw")}
    near = next(c for c in r.clearances if c["b"] == "s_yaw")
    assert near["n_near"] == 9 and 0 <= near["min_mm"] < 5
    # pairs: base-arm, base-s_yaw (same rigid group: rest only), arm-s_yaw -> 3 + 8 * 2
    assert r.n_checks == 3 + 8 * 2
    assert r.reach_mm == pytest.approx(100.0, abs=1e-6)      # tool 100 mm from the horn axis
    m = r.masses
    assert m["s_yaw"]["mass_kg"] == pytest.approx(0.055) and m["arm"]["source"] == "printed PETG"
    assert 0.005 < m["arm"]["mass_kg"] < 0.03
    assert r.torques["j_yaw"]["max_abs_Nm"] == pytest.approx(0.0, abs=1e-9)   # vertical axis
    assert set(r.frames) == {"base", "arm", "s_yaw"}


def test_check_assembly_reports_overlaps(parts):
    tmp, plan, _, bad = parts
    r = check_assembly(plan, bad, tmp / "io")
    assert not r.ok
    pairs = {s.location: s for s in r.signals if s.gate == "L5"}
    # servo case through an unpocketed base (rest only) and the spline tip into an arm without a horn recess
    assert set(pairs) == {"base <-> s_yaw", "arm <-> s_yaw"}, r.feedback
    assert pairs["base <-> s_yaw"].evidence["n_poses"] == 1
    assert pairs["arm <-> s_yaw"].evidence["n_poses"] == 9
    # spline d 5.8 pokes 1 mm (spline_top - horn face) into the arm
    assert pairs["arm <-> s_yaw"].evidence["volume_mm3"] == pytest.approx(math.pi * 2.9 ** 2 * 1.0, rel=1e-3)
    # located in each owner's part frame: a 5.8 x 5.8 x 1 mm box in the arm frame, also named in the cause
    bb = pairs["arm <-> s_yaw"].evidence["bbox_mm"]["arm"]
    assert [round(bb[3] - bb[0], 1), round(bb[4] - bb[1], 1), round(bb[5] - bb[2], 1)] == [5.8, 5.8, 1.0]
    assert "in arm frame" in pairs["arm <-> s_yaw"].cause and "in s_yaw frame" in pairs["arm <-> s_yaw"].cause


def test_check_assembly_runs_authored_tests(parts):
    tmp, plan, good, _ = parts
    plan = plan.model_copy(update={"tests": AssemblyPlan.model_validate({**plan.model_dump(), "tests": [
        {"expect": "contact", "a": "s_yaw", "b": "base", "near": "base.servo_mount"},
        {"expect": "clearance", "a": "arm", "b": "base", "min_mm": 20.0, "over_joint": "j_yaw", "samples": 3},
        {"expect": "reach", "op": "<=", "value_mm": 90},
        {"expect": "allow_overlap", "a": "arm", "b": "s_yaw", "reason": "spline tip in the horn recess"}]}).tests})
    r = check_assembly(plan, good, tmp / "io")
    assert [t["ok"] for t in r.tests] == [True, False, False, True], r.tests
    # within 15 mm of the mount only the case is in reach (the flange is wider): the pocket's 0.3 mm sliding fit
    assert r.tests[0]["min_mm"] == pytest.approx(0.3, abs=1e-6)
    # arm underside at the horn face (base top 10 + horn face 15.9), the 120 mm plate below it at every yaw
    assert r.tests[1]["min_mm"] == pytest.approx(15.9, abs=0.01)
    assert r.tests[2]["reach_mm"] == pytest.approx(100.0, abs=1e-6)
    fails = {s.location: s.cause for s in r.signals if s.level == "failure"}
    assert set(fails) == {"arm <-> base", "plan.tests[2]"}, r.feedback
    # min clearance per moving pair; the exempt pair is not measured
    pairs = {(c["a"], c["b"]): c for c in r.clearances}
    assert set(pairs) == {("arm", "base")}
    assert pairs[("arm", "base")]["min_mm"] is None             # never within the 5 mm window
    assert "tests 2/4 pass" in r.feedback


def test_allow_overlap_exempts_pair(parts):
    tmp, plan, _, bad = parts
    plan = plan.model_copy(update={"allow_overlap": [("arm", "s_yaw"), ("s_yaw", "base")]})
    r = check_assembly(plan, bad, tmp / "io")
    assert r.ok and r.collisions == [], r.feedback


def test_check_print_on_real_parts(parts, tmp_path):
    tmp, plan, good, _ = parts
    r = check_print(plan, good, tmp / "io", tmp_path / "meshes")
    assert set(r.parts) == {"base", "arm"}
    for name, info in r.parts.items():
        assert info["watertight"] and info["fits"], (name, info)
    assert r.parts["arm"]["extents_mm"] == pytest.approx([130, 30, 6], abs=0.01)
    assert r.ok, r.feedback
