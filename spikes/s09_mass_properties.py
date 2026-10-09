"""S9: units and frame of Volume / CenterOfMass / MatrixOfInertia.

Known box a x b x c (mm). Analytic (density 1): V = abc, CoM_local = (a,b,c)/2,
I_cm = V/12 * diag(b2+c2, a2+c2, a2+b2) in the box's own axes.
Cases: plain shape, shape with non-identity Placement, Part::Box inside an App::Part with its own
placement, PartDesign Body pad. Compare reported values against local / placed / global candidates.
"""
import FreeCAD as App
import Part

A, B, C = 10.0, 20.0, 30.0
V = A * B * C
I_LOCAL = App.Matrix(V / 12 * (B * B + C * C), 0, 0, 0,
                     0, V / 12 * (A * A + C * C), 0, 0,
                     0, 0, V / 12 * (A * A + B * B), 0,
                     0, 0, 0, 1)
PLC = App.Placement(App.Vector(100, -50, 7), App.Rotation(30, 45, 10))
PART_PLC = App.Placement(App.Vector(-20, 300, 5), App.Rotation(0, 0, 90))


def mat3(m):
    return [[m.A11, m.A12, m.A13], [m.A21, m.A22, m.A23], [m.A31, m.A32, m.A33]]


def mat_close(m, n, tol=1e-6):
    return all(abs(x - y) < tol * max(1.0, abs(y)) for r, s in zip(mat3(m), mat3(n)) for x, y in zip(r, s))


def rotated_inertia(rot):
    R = rot.toMatrix()
    return R.multiply(I_LOCAL).multiply(R.transposed())


def report(shape, frames):
    """frames: name -> Placement of the box's local frame in that frame."""
    solid = shape.Solids[0]
    out = {"volume": shape.Volume, "volume_ok": abs(shape.Volume - V) < 1e-6,
           "com": list(solid.CenterOfMass), "I": mat3(solid.MatrixOfInertia),
           "mass_attr": getattr(solid, "Mass", None), "com_matches": [], "I_axes_match": []}
    for name, p in frames.items():
        if (solid.CenterOfMass - p.multVec(App.Vector(A / 2, B / 2, C / 2))).Length < 1e-6:
            out["com_matches"].append(name)
        if mat_close(solid.MatrixOfInertia, rotated_inertia(p.Rotation)):
            out["I_axes_match"].append(name)
    pp = solid.PrincipalProperties
    out["principal_moments"] = sorted(pp["Moments"]) if "Moments" in pp else str(pp)
    return out


cases = {}
cases["plain"] = report(Part.makeBox(A, B, C), {"local": App.Placement()})
s = Part.makeBox(A, B, C)
s.Placement = PLC
cases["shape_placement"] = report(s, {"local": App.Placement(), "placed": PLC})

doc = App.newDocument("S9")
part = doc.addObject("App::Part", "Part")
box = part.newObject("Part::Box", "Box")
box.Length, box.Width, box.Height = A, B, C
box.Placement = PLC
part.Placement = PART_PLC
doc.recompute()
frames = {"local": App.Placement(), "placed": PLC, "global": PART_PLC.multiply(PLC)}
cases["feature_in_part"] = report(box.Shape, frames)
cases["feature_in_part"]["getGlobalPlacement_is_global"] = box.getGlobalPlacement().isSame(PART_PLC.multiply(PLC), 1e-9)
cases["part_shape"] = report(part.Shape, frames)  # App::Part.Shape, if present

body = part.newObject("PartDesign::Body", "Body")
pbox = body.newObject("PartDesign::AdditiveBox", "PBox")
pbox.Length, pbox.Width, pbox.Height = A, B, C
body.Placement = PLC
doc.recompute()
cases["body_in_part"] = report(body.Shape, frames)
App.closeDocument(doc.Name)

result["cases"] = cases
result["units"] = "mm, mm^3, mm^5 (density 1); Mass attr = volume if present"
result["pass"] = all(c["volume_ok"] for c in cases.values()) and cases["plain"]["com_matches"] == ["local"]
