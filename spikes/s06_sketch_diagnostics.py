"""S6: what does the sketcher report for fully/under/conflicting/redundant sketches, headless?

Per case: a rectangle (4 lines, 4 coincidences) plus a case-specific constraint set. Record
solve() code, getattr DoF / FullyConstrained, the conflicting/redundant/partially-redundant/
malformed lists, getGeometryWithDependentParameters(), and the sketch's own State/recompute status.
Pass: the four cases are distinguishable from the recorded fields (documentation spike).
"""
import FreeCAD as App
import Part
import Sketcher

W, H = 30.0, 10.0
FIELDS = ["ConflictingConstraints", "RedundantConstraints", "PartiallyRedundantConstraints",
          "MalformedConstraints"]


def rectangle(sk):
    pts = [App.Vector(0, 0, 0), App.Vector(W, 0, 0), App.Vector(W, H, 0), App.Vector(0, H, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
    sk.addConstraint([Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1) for i in range(4)])


def base_full():
    # 4 coincident (above) + H,H,V,V + origin + width + height = fully constrained
    return [Sketcher.Constraint("Horizontal", 0), Sketcher.Constraint("Horizontal", 2),
            Sketcher.Constraint("Vertical", 1), Sketcher.Constraint("Vertical", 3),
            Sketcher.Constraint("Coincident", 0, 1, -1, 1),
            Sketcher.Constraint("DistanceX", 0, 1, 0, 2, W),
            Sketcher.Constraint("DistanceY", 1, 1, 1, 2, H)]


CASES = {
    "full": base_full,
    "under": lambda: base_full()[:-1],  # drop the height: 1 DoF
    "conflicting": lambda: base_full() + [Sketcher.Constraint("DistanceX", 2, 2, 2, 1, W + 5)],
    "redundant": lambda: base_full() + [Sketcher.Constraint("DistanceX", 2, 2, 2, 1, W)],
}


def probe(name, make):
    doc = App.newDocument("S6_" + name)
    body = doc.addObject("PartDesign::Body", "Body")
    sk = body.newObject("Sketcher::SketchObject", "Sketch")
    sk.AttachmentSupport = [(body.Origin.OriginFeatures[3], "")]
    sk.MapMode = "FlatFace"
    rectangle(sk)
    cons = make()
    first = sk.ConstraintCount
    sk.addConstraint(cons)
    rec = {"n_geometry": sk.GeometryCount, "n_constraints": sk.ConstraintCount,
           "added_index_range": [first, sk.ConstraintCount - 1]}
    rec["solve"] = sk.solve()
    doc.recompute()
    for attr in ["DoF", "FullyConstrained"] + FIELDS:
        try:
            v = getattr(sk, attr)
            rec[attr] = list(v) if isinstance(v, (list, tuple)) else v
        except Exception as e:
            rec[attr] = "n/a: %s" % type(e).__name__
    try:
        rec["dependent"] = [list(x) for x in sk.getGeometryWithDependentParameters()]
    except Exception as e:
        rec["dependent"] = "n/a: %s: %s" % (type(e).__name__, e)
    rec["state"] = list(sk.State)
    rec["isValid"] = sk.isValid()
    rec["status_msg"] = sk.getStatusString() if hasattr(sk, "getStatusString") else None
    App.closeDocument(doc.Name)
    return rec


cases = {}
for name, make in CASES.items():
    try:
        cases[name] = probe(name, make)
    except Exception as e:
        cases[name] = {"error": "%s: %s" % (type(e).__name__, e)}
result["cases"] = cases


def sig(r):
    return (r.get("solve"), r.get("DoF"), bool(r.get("ConflictingConstraints")),
            bool(r.get("RedundantConstraints")), bool(r.get("dependent")))


sigs = [sig(r) for r in cases.values() if "error" not in r]
result["distinguishable"] = len(sigs) == 4 and len(set(sigs)) == 4
result["pass"] = result["distinguishable"]
