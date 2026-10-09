"""S4: are joint Placement1/2 local or global? Plus a 2-joint chain driven via Offset1 (S3 method).

Setup: App::Part P (non-identity placement) holding a Part::Box (non-identity placement) and an LCS.
Candidate frames for PlacementN, compared against the known world point/frame:
  global          PlacementN
  part            P.Placement * PlacementN
  sub_object      getGlobalPlacement(sub-object) * PlacementN    (sub-object = Box or LCS)
  utils           UtilsAssembly.getGlobalPlacement(ref) * PlacementN
"""
import math

import FreeCAD as App
import JointObject
import UtilsAssembly


def close(p, q, tol=1e-6):
    return (p.Base - q.Base).Length < tol and p.Rotation.isSame(q.Rotation, 1e-9)


def candidates(ref, plc):
    part = ref[0]
    sub_obj = UtilsAssembly.getObject(ref)
    return {
        "global": plc,
        "part": part.Placement.multiply(plc),
        "sub_object": sub_obj.getGlobalPlacement().multiply(plc),
        "utils": UtilsAssembly.getGlobalPlacement(ref).multiply(plc),
    }


doc = App.newDocument("S4")
asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
joints = asm.newObject("Assembly::JointGroup", "Joints")


def make_part(name, part_plc, box_plc, lcs_plc):
    part = asm.newObject("App::Part", name)
    box = part.newObject("Part::Box", name + "_Box")
    box.Length, box.Width, box.Height = 20, 10, 5
    box.Placement = box_plc
    lcs = part.newObject("App::LocalCoordinateSystem", "LCS_" + name)
    lcs.Placement = lcs_plc
    part.Placement = part_plc
    return part, box, lcs


P, boxP, lcsP = make_part("P", App.Placement(App.Vector(7, 3, -2), App.Rotation(20, 0, 10)),
                          App.Placement(App.Vector(4, 0, 1), App.Rotation(0, 0, 30)),
                          App.Placement(App.Vector(10, 2, 3), App.Rotation(0, 45, 0)))
C, boxC, lcsC = make_part("C", App.Placement(App.Vector(60, 0, 0), App.Rotation(0, 0, 0)),
                          App.Placement(), App.Placement(App.Vector(0, 0, 5), App.Rotation()))
doc.recompute()
g = joints.newObject("App::FeaturePython", "Ground")
JointObject.GroundedJoint(g, P)

# --- Face/vertex reference: Vertex1 of the box, read in world from the solved shape
face_j = joints.newObject("App::FeaturePython", "FaceFixed")
JointObject.Joint(face_j, JointObject.JointTypes.index("Fixed"))
face_ref = [P, [boxP.Name + ".Face6", boxP.Name + ".Vertex8"]]
face_j.Proxy.setJointConnectors(face_j, [face_ref, [C, [lcsC.Name + ".", lcsC.Name + "."]]])
asm.solve()
doc.recompute()
# World position of Vertex8 of the box: box shape is in part coords, part placement on top
v8_world = P.Placement.multVec(boxP.Shape.Vertexes[7].Point)
face_cands = candidates(face_ref, face_j.Placement1)
result["face_ref"] = {
    "placement1": str(face_j.Placement1),
    "vertex8_world": str(v8_world),
    "matches_vertex": [k for k, c in face_cands.items() if (c.Base - v8_world).Length < 1e-6],
}

# --- LCS reference
lcs_ref = [P, [lcsP.Name + ".", lcsP.Name + "."]]
lcs_world = lcsP.getGlobalPlacement()
lcs_cands = candidates(lcs_ref, App.Placement())  # Placement1 is identity for whole-LCS refs (S2)
result["lcs_ref"] = {
    "lcs_world": str(lcs_world),
    "matches_lcs": [k for k, c in lcs_cands.items() if close(c, lcs_world)],
}
face_j.Suppressed = True
asm.solve()

# --- 2-joint chain: P(grounded) -R1-> C -R2-> D, driven via Offset1
D, boxD, lcsD = make_part("D", App.Placement(App.Vector(0, 80, 0), App.Rotation(10, 20, 30)),
                          App.Placement(), App.Placement(App.Vector(0, 0, 0), App.Rotation()))
lcsC2 = C.newObject("App::LocalCoordinateSystem", "LCS_C_out")
lcsC2.Placement = App.Placement(App.Vector(20, 5, 5), App.Rotation(0, -90, 0))
doc.recompute()


def revolute(name, pa, la, pb, lb):
    j = joints.newObject("App::FeaturePython", name)
    JointObject.Joint(j, JointObject.JointTypes.index("Revolute"))
    j.Proxy.setJointConnectors(j, [[pa, [la.Name + ".", la.Name + "."]], [pb, [lb.Name + ".", lb.Name + "."]]])
    return j


r1 = revolute("R1", P, lcsP, C, lcsC)
r2 = revolute("R2", C, lcsC2, D, lcsD)
asm.solve()


def rz(q):
    return App.Placement(App.Vector(), App.Rotation(App.Vector(0, 0, 1), q))


def rot_err_deg(r_got, r_exp):
    a = math.degrees(r_exp.inverted().multiply(r_got).Angle)
    return min(a, 360 - a)


POSES = [(0, 0), (30, -60), (90, 45), (-150, 120), (179, -179), (60, 175), (-90, -90)]


def run_chain(mode):
    """offset_only: set Offset1 = Rz(q) and solve.
    preplace: also write our own FK placements (mating rule) before the final solve."""
    rows = []
    for q1, q2 in POSES:
        r1.Offset1 = rz(q1)
        r2.Offset1 = rz(q2)
        # Expected by the mating rule, composed down the chain
        exp_c = lcsP.getGlobalPlacement().multiply(rz(q1))            # LCS_C world
        c_part = exp_c.multiply(lcsC.Placement.inverse())               # C part world
        exp_d = c_part.multiply(lcsC2.Placement).multiply(rz(q2))       # LCS_D world
        if mode == "preplace":
            C.Placement = c_part
            D.Placement = exp_d.multiply(lcsD.Placement.inverse())
        code = asm.solve()
        doc.recompute()
        row = {
            "q": [q1, q2], "solve": code,
            "err_C_mm": (lcsC.getGlobalPlacement().Base - exp_c.Base).Length,
            "err_D_mm": (lcsD.getGlobalPlacement().Base - exp_d.Base).Length,
            "rot_err_C_deg": rot_err_deg(lcsC.getGlobalPlacement().Rotation, exp_c.Rotation),
            "rot_err_D_deg": rot_err_deg(lcsD.getGlobalPlacement().Rotation, exp_d.Rotation),
        }
        row["ok"] = (row["solve"] == 0 and row["err_C_mm"] < 1e-6 and row["err_D_mm"] < 1e-6
                     and row["rot_err_C_deg"] < 1e-6 and row["rot_err_D_deg"] < 1e-6)
        rows.append(row)
    return {"pass": all(r["ok"] for r in rows), "rows": rows}


result["chain"] = {m: run_chain(m) for m in ("offset_only", "preplace")}

result["pass"] = (
    "utils" in result["face_ref"]["matches_vertex"]
    and "utils" in result["lcs_ref"]["matches_lcs"]
    and result["chain"]["preplace"]["pass"]
)
