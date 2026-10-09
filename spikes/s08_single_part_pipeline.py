"""S8: full single-part pipeline, headless.

Params(plate_w, plate_d, thick, hole_d) -> Body -> constrained plate sketch on XY_Plane -> Pad(thick)
-> datum plane at z = thick -> constrained circle sketch on it -> Pocket ThroughAll
-> recompute -> save .FCStd -> STEP + STL -> change Params -> recompute -> volume follows
-> reopen: same feature tree + expressions; STEP/STL read back with matching volume.
All cell values are written as "=<value> <unit>" (S7 finding).
"""
import math
import os

import FreeCAD as App
import Mesh
import MeshPart
import Part
import Sketcher

OUT = request.get("out_dir") or os.getcwd()
os.makedirs(OUT, exist_ok=True)
P0 = {"plate_w": 40.0, "plate_d": 25.0, "thick": 6.0, "hole_d": 8.0}
P1 = {"plate_w": 55.0, "plate_d": 25.0, "thick": 9.0, "hole_d": 12.0}


def expected_volume(p):
    return p["plate_w"] * p["plate_d"] * p["thick"] - math.pi * (p["hole_d"] / 2) ** 2 * p["thick"]


def set_params(sheet, p):
    for i, (k, v) in enumerate(p.items()):
        sheet.set("B%d" % (i + 1), "=%s mm" % v)


def bind(sk, name, idx, alias):
    sk.renameConstraint(idx, name)
    sk.setExpression(".Constraints." + name, "Params." + alias)


doc = App.newDocument("S8")
sheet = doc.addObject("Spreadsheet::Sheet", "Params")
for i, k in enumerate(P0):
    sheet.set("A%d" % (i + 1), k)
    sheet.setAlias("B%d" % (i + 1), k)
set_params(sheet, P0)
body = doc.addObject("PartDesign::Body", "Body")
xy = body.Origin.OriginFeatures[3]

# Plate sketch: rectangle centred on origin
sk = body.newObject("Sketcher::SketchObject", "PlateSketch")
sk.AttachmentSupport = [(xy, "")]
sk.MapMode = "FlatFace"
w, d = P0["plate_w"], P0["plate_d"]
pts = [App.Vector(-w / 2, -d / 2, 0), App.Vector(w / 2, -d / 2, 0), App.Vector(w / 2, d / 2, 0), App.Vector(-w / 2, d / 2, 0)]
for i in range(4):
    sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
cons = [Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1) for i in range(4)]
cons += [Sketcher.Constraint("Horizontal", 0), Sketcher.Constraint("Horizontal", 2),
         Sketcher.Constraint("Vertical", 1), Sketcher.Constraint("Vertical", 3),
         Sketcher.Constraint("Symmetric", 0, 1, 2, 1, -1, 1)]  # diagonal corners symmetric about origin
sk.addConstraint(cons)
bind(sk, "w", sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, w)), "plate_w")
bind(sk, "d", sk.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, d)), "plate_d")

pad = body.newObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.setExpression("Length", "Params.thick")

# Datum plane at the top face, bound to the same param (no face reference => no TNP)
top = body.newObject("PartDesign::Plane", "TopPlane")
top.AttachmentSupport = [(xy, "")]
top.MapMode = "FlatFace"
top.setExpression(".AttachmentOffset.Base.z", "Params.thick")

hs = body.newObject("Sketcher::SketchObject", "HoleSketch")
hs.AttachmentSupport = [(top, "")]
hs.MapMode = "FlatFace"
hs.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), P0["hole_d"] / 2), False)
hs.addConstraint(Sketcher.Constraint("Coincident", 0, 3, -1, 1))
bind(hs, "hole", hs.addConstraint(Sketcher.Constraint("Diameter", 0, P0["hole_d"])), "hole_d")

pocket = body.newObject("PartDesign::Pocket", "Pocket")
pocket.Profile = hs
pocket.Type = "ThroughAll"
doc.recompute()


def snapshot(doc):
    b = doc.getObject("Body")
    return {
        "tree": [[o.Name, o.TypeId] for o in b.Group],
        "expressions": {o.Name: sorted([list(e) for e in o.ExpressionEngine]) for o in doc.Objects if o.ExpressionEngine},
        "invalid": [o.Name for o in doc.Objects if "Invalid" in o.State],
        "volume": b.Shape.Volume,
        "dof": {s.Name: s.solve() == 0 and s.FullyConstrained for s in doc.Objects if s.TypeId == "Sketcher::SketchObject"},
    }


s0 = snapshot(doc)
s0["volume_ok"] = abs(s0["volume"] - expected_volume(P0)) < 1e-6
s0["tip"] = body.Tip.Name

fcstd = os.path.join(OUT, "s08_plate.FCStd")
step = os.path.join(OUT, "s08_plate.step")
stl = os.path.join(OUT, "s08_plate.stl")
doc.saveAs(fcstd)
body.Shape.exportStep(step)
mesh = MeshPart.meshFromShape(Shape=body.Shape, LinearDeflection=0.05, AngularDeflection=0.2, Relative=False)
mesh.write(stl)
exports = {
    "step_volume": Part.read(step).Volume,
    "stl_volume": Mesh.Mesh(stl).Volume,
    "stl_facets": mesh.CountFacets,
    "stl_solid": mesh.isSolid(),
}
exports["step_ok"] = abs(exports["step_volume"] - s0["volume"]) < 1e-6
exports["stl_rel_err"] = abs(exports["stl_volume"] - s0["volume"]) / s0["volume"]

set_params(sheet, P1)
doc.recompute()
s1 = snapshot(doc)
s1["volume_ok"] = abs(s1["volume"] - expected_volume(P1)) < 1e-6
App.closeDocument(doc.Name)

doc2 = App.openDocument(fcstd)
s2 = snapshot(doc2)
s2["same_tree"] = s2["tree"] == s0["tree"]
s2["same_expressions"] = s2["expressions"] == s0["expressions"]
s2["volume_ok"] = abs(s2["volume"] - expected_volume(P0)) < 1e-6
set_params(doc2.getObject("Params"), P1)
doc2.recompute()
s2["volume_after_change_ok"] = abs(doc2.getObject("Body").Shape.Volume - expected_volume(P1)) < 1e-6
App.closeDocument(doc2.Name)

result.update({"initial": s0, "exports": exports, "after_param_change": s1, "reopened": s2})
result["pass"] = (s0["volume_ok"] and all(s0["dof"].values()) and not s0["invalid"]
                  and exports["step_ok"] and exports["stl_solid"] and exports["stl_rel_err"] < 1e-2
                  and s1["volume_ok"] and not s1["invalid"]
                  and s2["same_tree"] and s2["same_expressions"] and s2["volume_ok"] and s2["volume_after_change_ok"])
