"""T1.6: kinematic tree + FK (pure Python) and the assembly builder through the worker."""
import math

import pytest

from assembly import kinematics as K
from assembly.builder import build_assembly
from components.card import connectors, load_card
from harness.tools import compile_part
from schemas.plan import AssemblyPlan


def conn(name, o=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="pivot"):
    return {"name": name, "o": list(o), "z": list(z), "x": list(x), "label": label}


def chain_plan(**over):
    plan = {
        "ground": "base", "requirements": {"dof": 2}, "dof": 2,
        "parts": [
            {"name": "base", "intent": "plate", "connectors": [conn("top", (0, 0, 10))]},
            {"name": "upper", "intent": "link", "connectors": [conn("root"), conn("tip", (100, 0, 0), z=(0, 1, 0))]},
            {"name": "fore", "intent": "link", "connectors": [conn("root", (0, 0, 5)), conn("tool", (80, 0, 0), label="tool")]},
        ],
        "joints": [
            {"name": "j_yaw", "type": "revolute", "parent": "base.top", "child": "upper.root",
             "limits": {"lower": -1.6, "upper": 1.6, "effort": 1, "velocity": 1}},
            {"name": "j_elbow", "type": "revolute", "parent": "upper.tip", "child": "fore.root",
             "limits": {"lower": -2, "upper": 2, "effort": 1, "velocity": 1}},
        ],
    }
    plan.update(over)
    return AssemblyPlan.model_validate(plan).model_dump()


def frames_of(plan):
    return {p["name"]: {c["name"]: K.frame(c["o"], c["z"], c["x"]) for c in p["connectors"]} for p in plan["parts"]}


def close(a, b, tol=1e-9):
    return all(abs(x - y) < tol for x, y in zip(a, b))


# ---- pure kinematics ------------------------------------------------------------------------
def test_frame_math():
    t = K.mul(K.frame((1, 2, 3), (0, 1, 0), (1, 0, 0)), K.rz(0.7))
    assert K.residual(K.mul(t, K.inv(t)), K.identity()) == pytest.approx((0, 0), abs=1e-6)
    assert K.residual(K.rz(0), K.rz(math.radians(30)))[1] == pytest.approx(30)
    assert K.origin(K.mul(K.tz(5), K.tz(7))) == [0, 0, 12]


def test_fk_two_link_chain():
    plan = chain_plan()
    tree = K.Tree(plan, frames_of(plan))
    assert tree.ok, tree.signals
    assert [e.name for e in tree.edges] == ["j_yaw", "j_elbow"]
    q, sigs = tree.joint_values({"j_yaw": math.pi / 2, "j_elbow": 0.5})
    assert sigs == []
    world = tree.fk(q)
    # upper: root on base.top rotated 90 deg about z -> its tip (100, 0, 0) lands at (0, 100, 10)
    tip = K.mul(world["upper"], frames_of(plan)["upper"]["tip"])
    assert close(K.origin(tip), [0, 100, 10])
    # fore root sits on upper.tip, rotated by the elbow about the tip's z (= upper's +y = world -x)
    root = K.mul(world["fore"], frames_of(plan)["fore"]["root"])
    assert K.residual(root, K.mul(tip, K.rz(0.5))) == pytest.approx((0, 0), abs=1e-6)


def test_joint_values_limits_and_mimic():
    plan = chain_plan()
    plan["joints"][1]["mimic"] = {"joint": "j_yaw", "multiplier": -2, "offset": 0.1}
    tree = K.Tree(plan, frames_of(plan))
    q, sigs = tree.joint_values({"j_yaw": 0.5})
    assert q == {"j_yaw": 0.5, "j_elbow": pytest.approx(-0.9)} and sigs == []
    _, sigs = tree.joint_values({"j_yaw": 3, "nope": 1})
    causes = " | ".join(s["cause"] for s in sigs)
    assert "outside its limits" in causes and "unknown joint 'nope'" in causes


@pytest.mark.parametrize("mutate, needle", [
    (lambda p: p["joints"][1].update(child="upper.root"), "parent and child are both on 'upper'"),
    (lambda p: p["joints"].append(dict(p["joints"][1], name="j_extra", parent="base.top")), "two parents"),
    (lambda p: p["joints"][1].update(child="base.top", parent="fore.root"), "ground 'base' cannot be a child"),
    (lambda p: p["joints"].pop(), "'fore' is not connected to the ground"),
    (lambda p: p["joints"][0].update(parent="base.nope"), "has no connector 'nope'"),
    (lambda p: p.update(ground="ghost"), "ground 'ghost' is not a part"),
])
def test_tree_signals(mutate, needle):
    plan = chain_plan()
    mutate(plan)
    tree = K.Tree(plan, frames_of(plan))
    assert not tree.ok
    assert any(needle in s["cause"] and s["gate"] == "L5" for s in tree.signals), tree.signals


def test_broken_ref_reported_once():
    plan = chain_plan()
    plan["joints"][0]["parent"] = "base.nope"
    tree = K.Tree(plan, frames_of(plan))
    assert [s["cause"] for s in tree.signals] == ["plan.joints.j_yaw: 'base' has no connector 'nope' (has: top)"]


# ---- worker: real parts, a servo card model, an envelope card, a slider -----------------------
BASE = '''from agenticcad import Part


def build(P):
    part = Part("base")
    p = part.params(side=120, thick=10)
    part.pad(part.sketch("plate", on="XY").rect(p.side, p.side), p.thick)
    part.connector("servo_mount", origin=(0, 0, p.thick), z=(0, 0, 1), x=(1, 0, 0), label="servo_mount")
    part.connector("ebay_mount", origin=(0, -30, p.thick), z=(0, 0, 1), x=(1, 0, 0), label="ebay_mount")
    return part
'''

ARM = '''from agenticcad import Part


def build(P):
    part = Part("arm")
    p = part.params(length=100, width=20, thick=6)
    part.pad(part.sketch("profile", on="XY").slot((0, 0), (p.length, 0), p.width), p.thick)
    part.connector("horn_seat", origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="servo_horn")
    part.connector("rail", origin=(p.length, 0, p.thick), z=(1, 0, 0), x=(0, 0, 1), label="slider")
    return part
'''

CARRIAGE = '''from agenticcad import Part


def build(P):
    part = Part("carriage")
    p = part.params(side=15)
    part.pad(part.sketch("block", on="XY").rect(p.side, p.side), p.side)
    part.connector("rail_in", origin=(0, 0, 0), z=(1, 0, 0), x=(0, 0, 1), label="slider")
    return part
'''


def mech_plan():
    return AssemblyPlan.model_validate({
        "ground": "base", "requirements": {"dof": 2}, "dof": 2,
        "components": [{"id": "s_yaw", "card": "servo/mg996r", "attach_to": "base.servo_mount"},
                       {"id": "ebay", "card": "electronics/generic_bay", "attach_to": "base.ebay_mount"}],
        "parts": [
            {"name": "base", "intent": "plate", "connectors": [conn("servo_mount", (0, 0, 10), label="servo_mount"),
                                                               conn("ebay_mount", (0, -30, 10), label="ebay_mount")]},
            {"name": "arm", "intent": "bar", "connectors": [conn("horn_seat", label="servo_horn"),
                                                            conn("rail", (100, 0, 6), (1, 0, 0), (0, 0, 1), "slider")]},
            {"name": "carriage", "intent": "block", "connectors": [conn("rail_in", z=(1, 0, 0), x=(0, 0, 1), label="slider")]},
        ],
        "joints": [
            {"name": "j_yaw", "type": "revolute", "parent": "s_yaw.horn", "child": "arm.horn_seat", "driver": "s_yaw",
             "limits": {"lower": -1.57, "upper": 1.57}},
            {"name": "j_slide", "type": "slider", "parent": "arm.rail", "child": "carriage.rail_in",
             "limits": {"lower": 0, "upper": 30, "effort": 10, "velocity": 20}},
        ],
    })


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("asm")
    files = {}
    for name, src in (("base", BASE), ("arm", ARM), ("carriage", CARRIAGE)):
        prog = tmp / "parts" / f"{name}.py"
        prog.parent.mkdir(exist_ok=True)
        prog.write_text(src, encoding="utf-8")
        r = compile_part(prog, tmp / "io", out=tmp / "build" / f"{name}.FCStd")
        assert r.ok, r.feedback
        files[name] = r.fcstd
    poses = [{}, {"j_yaw": 0.6, "j_slide": 20}, {"j_yaw": -1.5, "j_slide": 5}]
    res = build_assembly(mech_plan(), files, tmp / "io", tmp / "build" / "assembly.FCStd", poses)
    return res, poses


def test_build_assembly_poses_and_residuals(built):
    res, poses = built
    assert res.ok, res.feedback
    assert res.fcstd and res.fcstd.endswith("assembly.FCStd")
    assert [e["name"] for e in res.tree] == ["s_yaw", "ebay", "j_yaw", "j_slide"] or \
        {e["name"] for e in res.tree} == {"s_yaw", "ebay", "j_yaw", "j_slide"}
    assert len(res.poses) == 3
    for row in res.poses:
        assert row["solve"] == 0
        assert row["max_pos_mm"] < 1e-4 and row["max_rot_deg"] < 1e-4, row


def test_build_assembly_matches_mating_rule(built):
    res, _ = built
    plan = mech_plan().model_dump()
    card = connectors(load_card("servo/mg996r"))
    horn = K.frame(card["horn"]["o"], card["horn"]["z"], card["horn"]["x"])
    # saved pose = poses[0] (rest): arm.horn_seat on the servo horn face, servo on base.servo_mount
    servo = K.frame((0, 0, 10), (0, 0, 1), (1, 0, 0))
    assert K.residual(res.owners["s_yaw"]["placement"], servo) == pytest.approx((0, 0), abs=1e-6)
    assert K.residual(res.owners["arm"]["placement"], K.mul(servo, horn)) == pytest.approx((0, 0), abs=1e-6)
    # carriage slides along the arm's +x rail: at rest its block starts at the arm tip
    car = res.owners["carriage"]["placement"]
    assert close(K.origin(car), K.origin(K.mul(K.mul(servo, horn), K.frame((100, 0, 6), (1, 0, 0), (0, 0, 1)))), 1e-6)
    assert plan["ground"] == "base"
    # the generic bay has no generator: its envelope box sits on base.ebay_mount (bottom at standoff_h)
    bb = res.owners["ebay"]["bbox"]
    assert bb == pytest.approx([-37.5, -60.0, 16.0, 37.5, 0.0, 41.0], abs=1e-6)
    # link bboxes are world too: the arm (slot 0..100, width 20, 6 thick) sits on the horn face at z 25.9
    assert res.owners["arm"]["bbox"] == pytest.approx([0.0, -10.0, 25.9, 120.0, 10.0, 31.9], abs=1e-6)


def test_build_assembly_reports_tree_errors(tmp_path):
    plan = mech_plan()
    plan.joints[1].parent = "arm.nope"
    res = build_assembly(plan, {}, tmp_path / "io", tmp_path / "assembly.FCStd")
    assert not res.ok
    causes = " | ".join(s.cause for s in res.signals)
    assert "no compiled .FCStd" in causes


def test_saved_assembly_reopens_and_follows_param_edit(built, tmp_path):
    from pathlib import Path

    from harness.run import run_request
    res, _ = built
    script = Path(__file__).parent / "freecad" / "assembly_reopen.py"
    r = run_request({"op": "run_script", "script": str(script), "fcstd": res.fcstd}, tmp_path / "io")
    assert r.ok, [s.cause + s.location for s in r.signals]
    out = r.result
    assert set(out["links"]) == {"base", "arm", "carriage", "s_yaw"} and all(out["links"].values())
    assert set(out["joints"]) == {"Ground", "j_yaw", "j_slide", "attach_s_yaw", "attach_ebay"}
    assert out["solve"] == 0 and out["solve_after_edit"] == 0
    # arm length 100 -> 150, base thick 10 -> 14: the arm rides up with the servo (fixed + revolute) and
    # the rail tip moves out; the carriage stays on the rail axis (slider), aligned
    assert out["rail"] == pytest.approx([150 + 10, 0, 31.9 + 4], abs=1e-6)  # horn axis at shaft_x = 10
    assert out["off_axis_mm"] < 1e-6 and out["rot_deg"] < 1e-6
