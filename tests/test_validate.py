"""T1.7: pose grid, static loads, plan validator (pure Python) and the L5/L6/L7 gates through the worker."""
import math

import pytest
import trimesh

from assembly import kinematics as K
from harness.tools import compile_part
from profiles import load_profile
from schemas.plan import AssemblyPlan
from validate.assembly_check import check_assembly
from validate.masses import printed_mass_kg
from validate.plan_check import check_plan
from validate.poses import grid
from validate.printability import check_mesh, check_print
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
    _, sigs = check_mesh("big", trimesh.creation.box((190, 20, 20)), prof)
    assert [s.cause for s in sigs if "build volume" in s.cause]
    _, sigs = check_mesh("diag_ok", trimesh.creation.box((175, 10, 10)), prof)   # fits along any axis
    assert sigs == []
    _, sigs = check_mesh("thin", trimesh.creation.box((40, 30, 0.8)), prof)
    assert any("walls down to 0.80" in s.cause for s in sigs)
    open_box = trimesh.creation.box((10, 10, 10))
    open_box.update_faces(list(range(len(open_box.faces) - 2)))
    _, sigs = check_mesh("open", open_box, prof)
    assert any("not a closed solid" in s.cause for s in sigs)


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
