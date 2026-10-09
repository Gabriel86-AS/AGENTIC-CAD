"""S3: how to drive a revolute joint to angle q headless?

Mating rule (P0 §4): LCS_child_world(q) == LCS_parent_world * Rz(q).
Methods tried, each over a sequence of angles (sequence matters: a free DoF may keep the old pose):
  offset1   Offset1 = Rz(q)
  offset2   Offset2 = Rz(-q)
  limits    EnableAngleMin/Max, AngleMin = AngleMax = q
Pass: some method gives FK error < 1e-6 for every q in the sequence.
"""
import math

import FreeCAD as App
import JointObject

SEQ = [30.0, 90.0, -45.0, 170.0, 0.0, -120.0]


def make_part(asm, name, part_plc, lcs_plc):
    part = asm.newObject("App::Part", name)
    box = part.newObject("Part::Box", name + "_Box")
    box.Length, box.Width, box.Height = 20, 10, 5
    lcs = part.newObject("App::LocalCoordinateSystem", "LCS_" + name)
    lcs.Placement = lcs_plc
    part.Placement = part_plc
    return part, lcs


def fk_error(lcs_a, lcs_b, q):
    expected = lcs_a.getGlobalPlacement().multiply(App.Placement(App.Vector(), App.Rotation(App.Vector(0, 0, 1), q)))
    got = lcs_b.getGlobalPlacement()
    pos = (expected.Base - got.Base).Length
    # Rotation error as angle of expected^-1 * got
    d = expected.Rotation.inverted().multiply(got.Rotation)
    return pos, abs(math.degrees(d.Angle)) if d.Angle <= math.pi else 360 - math.degrees(d.Angle)


def run_method(method):
    doc = App.newDocument("S3_" + method)
    asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
    joints = asm.newObject("Assembly::JointGroup", "Joints")
    a, lcs_a = make_part(asm, "A", App.Placement(App.Vector(5, -3, 2), App.Rotation(30, 10, 0)),
                         App.Placement(App.Vector(20, 5, 5), App.Rotation(0, 90, 0)))
    b, lcs_b = make_part(asm, "B", App.Placement(App.Vector(100, 40, -7), App.Rotation(-60, 0, 25)),
                         App.Placement(App.Vector(0, 5, 0), App.Rotation(45, 0, 180)))
    doc.recompute()
    g = joints.newObject("App::FeaturePython", "Ground")
    JointObject.GroundedJoint(g, a)
    j = joints.newObject("App::FeaturePython", "Revolute")
    JointObject.Joint(j, JointObject.JointTypes.index("Revolute"))
    j.Proxy.setJointConnectors(j, [[a, [lcs_a.Name + ".", lcs_a.Name + "."]], [b, [lcs_b.Name + ".", lcs_b.Name + "."]]])
    asm.solve()

    rows = []
    for q in SEQ:
        rz = App.Rotation(App.Vector(0, 0, 1), q)
        if method == "offset1":
            j.Offset1 = App.Placement(App.Vector(), rz)
        elif method == "offset2":
            j.Offset2 = App.Placement(App.Vector(), rz.inverted())
        elif method == "limits":
            j.EnableAngleMin = True
            j.EnableAngleMax = True
            j.AngleMin = q
            j.AngleMax = q
        code = asm.solve()
        doc.recompute()
        pos, ang = fk_error(lcs_a, lcs_b, q)
        rows.append({"q": q, "solve": code, "pos_err_mm": pos, "rot_err_deg": ang})
    App.closeDocument(doc.Name)
    ok = all(r["solve"] == 0 and r["pos_err_mm"] < 1e-6 and r["rot_err_deg"] < 1e-6 for r in rows)
    return {"pass": ok, "rows": rows}


methods = {}
for m in ("offset1", "offset2", "limits"):
    try:
        methods[m] = run_method(m)
    except Exception as e:
        methods[m] = {"pass": False, "error": "%s: %s" % (type(e).__name__, e)}
result["methods"] = methods
result["working_methods"] = [m for m, v in methods.items() if v["pass"]]
result["pass"] = bool(result["working_methods"])
