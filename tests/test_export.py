"""T1.8: URDF exporter + description checks D1, D2, D4 on a 2-DOF yaw + shoulder arm built from real parts."""
import math
import xml.etree.ElementTree as ET

import numpy as np
import pytest
import yourdfpy

from assembly.kinematics import Tree, mul
from description.checks import check_description
from export.urdf import export_package, rpy
from harness.tools import compile_part
from schemas.plan import AssemblyPlan
from validate.assembly_check import check_assembly
from validate.poses import grid
from validate.statics import joint_loads


def conn(name, o=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="pivot"):
    return {"name": name, "o": list(o), "z": list(z), "x": list(x), "label": label}


BASE = '''from agenticcad import Part


def build(P):
    part = Part("base")
    p = part.params(side=120, thick=10)
    part.pad(part.sketch("plate", on="XY").rect(p.side, p.side), p.thick)
    part.connector("servo_mount", origin=(0, 0, p.thick), z=(0, 0, 1), x=(1, 0, 0), label="servo_mount")
    part.mount("s_yaw", at="servo_mount")
    return part
'''

TURRET = '''from agenticcad import Part


def build(P):
    part = Part("turret")
    p = part.params(length=60, width=30, thick=6)
    part.pad(part.sketch("profile", on="XY").slot((0, 0), (p.length, 0), p.width), p.thick)
    part.connector("horn_seat", origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="servo_horn")
    part.connector("sh_mount", origin=(p.length, 0, 40), z=(0, 1, 0), x=(1, 0, 0), label="servo_mount")
    part.mate_to("s_yaw.horn", at="horn_seat")
    return part
'''

ARM = '''from agenticcad import Part


def build(P):
    part = Part("arm")
    p = part.params(length=120, width=24, thick=6)
    part.pad(part.sketch("profile", on="XY").slot((0, 0), (p.length, 0), p.width), p.thick)
    part.connector("horn_seat", origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="servo_horn")
    part.connector("tip", origin=(p.length, 0, p.thick), z=(0, 0, 1), x=(1, 0, 0), label="tool")
    part.mate_to("s_sh.horn", at="horn_seat")
    return part
'''


def plan():
    return AssemblyPlan.model_validate({
        "ground": "base", "dof": 2, "requirements": {"dof": 2, "reach_mm": 100, "payload_g": 50, "pack": "manipulator"},
        "components": [{"id": "s_yaw", "card": "servo/mg996r", "attach_to": "base.servo_mount"},
                       {"id": "s_sh", "card": "servo/mg996r", "attach_to": "turret.sh_mount"},
                       {"id": "ebay", "card": "electronics/generic_bay", "attach_to": "base.servo_mount"}],
        "parts": [
            {"name": "base", "intent": "plate", "connectors": [conn("servo_mount", (0, 0, 10), label="servo_mount")]},
            {"name": "turret", "intent": "bar", "connectors": [conn("horn_seat", label="servo_horn"),
                                                               conn("sh_mount", (60, 0, 40), (0, 1, 0), label="servo_mount")]},
            {"name": "arm", "intent": "bar", "connectors": [conn("horn_seat", label="servo_horn"),
                                                            conn("tip", (120, 0, 6), label="tool")]},
        ],
        "joints": [
            {"name": "j_yaw", "type": "revolute", "parent": "s_yaw.horn", "child": "turret.horn_seat", "driver": "s_yaw",
             "limits": {"lower": -1.57, "upper": 1.57}},
            {"name": "j_sh", "type": "revolute", "parent": "s_sh.horn", "child": "arm.horn_seat", "driver": "s_sh",
             "limits": {"lower": -1.2, "upper": 1.2}},
        ],
        # geometry here only feeds the exporter; the bay envelope sits on the yaw servo on purpose
        "allow_overlap": [["ebay", "s_yaw"], ["ebay", "base"], ["ebay", "turret"], ["s_sh", "turret"]],
    })


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("exp")
    p = plan()
    files = {}
    for name, src in (("base", BASE), ("turret", TURRET), ("arm", ARM)):
        prog = tmp / "parts" / f"{name}.py"
        prog.parent.mkdir(exist_ok=True)
        prog.write_text(src, encoding="utf-8")
        r = compile_part(prog, tmp / "io", out=tmp / "build" / f"{name}.FCStd", plan=p)
        assert r.ok, r.feedback
        files[name] = r.fcstd
    check = check_assembly(p, files, tmp / "io")
    res = export_package(p, files, check, tmp / "io", tmp / "release", robot="b13")
    return p, check, res


def test_rpy_roundtrip():
    from scipy.spatial.transform import Rotation
    for angles in ([0.1, -0.4, 2.0], [0.0, math.pi / 2, 0.3], [1.0, -math.pi / 2, 0.0]):
        R = Rotation.from_euler("xyz", angles).as_matrix()
        back = Rotation.from_euler("xyz", rpy(R.tolist())).as_matrix()
        assert np.allclose(R, back, atol=1e-9)


def test_export_structure(exported):
    p, check, res = exported
    assert res.ok, [s.cause for s in res.signals]
    assert set(res.links) == {"base", "turret", "arm"}
    assert res.links["base"]["owners"] == ["base", "ebay", "s_yaw"]
    assert res.links["turret"]["owners"] == ["s_sh", "turret"]
    assert res.joints == {"j_yaw": {"type": "revolute", "parent": "base", "child": "turret"},
                          "j_sh": {"type": "revolute", "parent": "turret", "child": "arm"}}
    root = ET.parse(res.urdf).getroot()
    j = {e.get("name"): e for e in root.iter("joint")}
    lim = j["j_sh"].find("limit")
    assert float(lim.get("effort")) == pytest.approx(0.54) and float(lim.get("velocity")) == pytest.approx(7.48)
    assert float(lim.get("lower")) == -1.2
    assert j["tool0_joint"].find("parent").get("link") == "arm"
    # link mass = printed parts + card masses (servo 55 g, bay 60 g)
    assert res.links["base"]["mass_kg"] == pytest.approx(check.masses["base"]["mass_kg"] + 0.055 + 0.060)
    assert set(res.print_stl) == {"base", "turret", "arm"}


def test_urdf_fk_matches_our_fk(exported):
    """yourdfpy tool0 pose == our FK (tool connector in world) at random poses."""
    p, check, res = exported
    pkg = res.package
    name = pkg.replace("\\", "/").split("/")[-1]
    robot = yourdfpy.URDF.load(res.urdf, filename_handler=lambda f: f.replace("package://%s" % name, pkg), load_meshes=False)
    tree = Tree(p.model_dump(), check.frames, check.mounts)
    rng = np.random.default_rng(1)
    for _ in range(5):
        q = {"j_yaw": rng.uniform(-1.5, 1.5), "j_sh": rng.uniform(-1.2, 1.2)}
        robot.update_cfg(q)
        world = tree.fk(tree.joint_values(q)[0])
        ours = np.array(mul(world["arm"], tree.frames["arm"]["tip"]))
        ours[:3, 3] *= 1e-3
        assert np.allclose(robot.get_transform("tool0", "base"), ours, atol=1e-9)


def test_description_checks(exported):
    p, check, res = exported
    tree = Tree(p.model_dump(), check.frames, check.mounts)
    poses = grid(p.model_dump(), 3, 9)
    masses = [(o, m["mass_kg"], m["com_mm"]) for o, m in check.masses.items()]   # no payload: the URDF has none
    expected = [joint_loads(tree, tree.joint_values(q)[0], masses) for q in poses]
    assert max(abs(v) for e in expected for v in e.values()) > 0.01   # the shoulder is loaded
    d = check_description(res.package, res.urdf, poses, expected)
    assert d.ok, [s.cause for s in d.signals]
    assert d.results["D2"]["gravity_max_err_Nm"] < 1e-6
    assert d.results["D1"]["yourdfpy"]["roots"] == ["base"]
