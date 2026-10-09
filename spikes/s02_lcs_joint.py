"""S2: can a joint reference an App::LocalCoordinateSystem inside an App::Part?

UtilsAssembly.getObject/findPlacement (1.1.4) treat a sub-name "LCS." (empty element)
as the whole LCS and return an identity placement, so the joint frame should be the LCS frame.
Pass: after solve, LCS_a and LCS_b world frames coincide (< 1e-6 mm), for a Fixed joint
(full frame incl. x) and for a Revolute joint (origin + z).
"""
import FreeCAD as App
import JointObject


def make_part(assembly, name, part_plc, lcs_plc):
    part = assembly.newObject("App::Part", name)
    box = part.newObject("Part::Box", name + "_Box")
    box.Length, box.Width, box.Height = 20, 10, 5
    lcs = part.newObject("App::LocalCoordinateSystem", "LCS_" + name)
    lcs.Placement = lcs_plc
    part.Placement = part_plc
    return part, lcs


def frames(lcs):
    g = lcs.getGlobalPlacement()
    return g, [g.Rotation.multVec(App.Vector(*v)) for v in ((1, 0, 0), (0, 0, 1))]


def run_case(joint_type, sub_spelling):
    doc = App.newDocument("S2_" + joint_type)
    asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
    joints = asm.newObject("Assembly::JointGroup", "Joints")
    a, lcs_a = make_part(
        asm, "A", App.Placement(App.Vector(5, -3, 2), App.Rotation(30, 10, 0)),
        App.Placement(App.Vector(20, 5, 5), App.Rotation(0, 90, 0)),
    )
    b, lcs_b = make_part(
        asm, "B", App.Placement(App.Vector(100, 40, -7), App.Rotation(-60, 0, 25)),
        App.Placement(App.Vector(0, 5, 0), App.Rotation(45, 0, 180)),
    )
    doc.recompute()
    g = joints.newObject("App::FeaturePython", "Ground")
    JointObject.GroundedJoint(g, a)
    j = joints.newObject("App::FeaturePython", joint_type)
    JointObject.Joint(j, JointObject.JointTypes.index(joint_type))
    sub_a = sub_spelling.format(lcs=lcs_a.Name)
    sub_b = sub_spelling.format(lcs=lcs_b.Name)
    j.Proxy.setJointConnectors(j, [[a, [sub_a, sub_a]], [b, [sub_b, sub_b]]])
    code = asm.solve()
    doc.recompute()

    ga, (xa, za) = frames(lcs_a)
    gb, (xb, zb) = frames(lcs_b)
    out = {
        "solve_code": code,
        "placement1": str(j.Placement1),
        "placement2": str(j.Placement2),
        "lcs_a_world": str(ga),
        "lcs_b_world": str(gb),
        "pos_err_mm": (ga.Base - gb.Base).Length,
        "z_dot": za.dot(zb),
        "x_dot": xa.dot(xb),
        "part_b_moved": (b.Placement.Base - App.Vector(100, 40, -7)).Length > 1e-6,
    }
    ok = code == 0 and out["part_b_moved"] and out["pos_err_mm"] < 1e-6 and out["z_dot"] > 1 - 1e-9
    if joint_type == "Fixed":
        ok = ok and out["x_dot"] > 1 - 1e-9
    out["pass"] = ok
    App.closeDocument(doc.Name)
    return out


cases = {}
for spelling in ("{lcs}.", "{lcs}"):
    for jt in ("Fixed", "Revolute"):
        key = "%s|%s" % (jt, spelling)
        try:
            cases[key] = run_case(jt, spelling)
        except Exception as e:
            cases[key] = {"pass": False, "error": "%s: %s" % (type(e).__name__, e)}
result["cases"] = cases
result["working_spellings"] = sorted({k.split("|")[1] for k, v in cases.items() if v["pass"]})
result["pass"] = cases["Fixed|{lcs}."]["pass"] and cases["Revolute|{lcs}."]["pass"]
