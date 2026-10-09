"""S5: expression path that binds a named sketch constraint to a spreadsheet alias.

Per candidate path: Body -> rectangle sketch (fully constrained, width constraint named 'len')
-> Pad 5 mm; Params.B1 alias 'upper_arm_len'; bind; change cell; recompute; check pad volume.
Also: binding survives save + reopen and still drives geometry.
Pass: at least one path makes a cell change move geometry, including after reopen.
"""
import os

import FreeCAD as App
import Part
import Sketcher

OUT = request.get("out_dir") or os.getcwd()
os.makedirs(OUT, exist_ok=True)

PATHS = [".Constraints.len", "Constraints.len", ".Constraints[{idx}]", "Constraints[{idx}]"]
H = 10.0
PAD = 5.0


def build(path_tpl, tag):
    doc = App.newDocument("S5_" + tag)
    sheet = doc.addObject("Spreadsheet::Sheet", "Params")
    sheet.set("B1", "50 mm")
    sheet.setAlias("B1", "upper_arm_len")
    body = doc.addObject("PartDesign::Body", "Body")
    sk = body.newObject("Sketcher::SketchObject", "Sketch")
    sk.AttachmentSupport = [(body.Origin.OriginFeatures[3], "")]  # XY_Plane
    sk.MapMode = "FlatFace"
    pts = [App.Vector(0, 0, 0), App.Vector(30, 0, 0), App.Vector(30, H, 0), App.Vector(0, H, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
    c = []
    for i in range(4):
        c.append(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
    c += [Sketcher.Constraint("Horizontal", 0), Sketcher.Constraint("Horizontal", 2),
          Sketcher.Constraint("Vertical", 1), Sketcher.Constraint("Vertical", 3),
          Sketcher.Constraint("Coincident", 0, 1, -1, 1)]  # corner on origin
    sk.addConstraint(c)
    idx = sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 30.0))
    sk.renameConstraint(idx, "len")
    sk.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, H))
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sk
    pad.Length = PAD
    doc.recompute()
    path = path_tpl.format(idx=idx)
    sk.setExpression(path, "Params.upper_arm_len")
    doc.recompute()
    return doc, sheet, sk, pad, path


def vol_ok(pad, length):
    return abs(pad.Shape.Volume - length * H * PAD) < 1e-6


cases = {}
for i, tpl in enumerate(PATHS):
    rec = {"path": tpl}
    try:
        doc, sheet, sk, pad, path = build(tpl, str(i))
        rec["expression_engine"] = [list(e) for e in sk.ExpressionEngine]
        rec["dof"] = sk.solve() and None or sk.getDatum("len").Value  # value after bind
        rec["vol_at_50"] = vol_ok(pad, 50.0)
        sheet.set("B1", "72 mm")
        doc.recompute()
        rec["datum_after_change"] = sk.getDatum("len").Value
        rec["vol_at_72"] = vol_ok(pad, 72.0)
        fn = os.path.join(OUT, "s05_case%d.FCStd" % i)
        doc.saveAs(fn)
        App.closeDocument(doc.Name)
        doc2 = App.openDocument(fn)
        sheet2, pad2 = doc2.getObject("Params"), doc2.getObject("Pad")
        sheet2.set("B1", "41 mm")
        doc2.recompute()
        rec["vol_at_41_after_reopen"] = vol_ok(pad2, 41.0)
        rec["engine_after_reopen"] = [list(e) for e in doc2.getObject("Sketch").ExpressionEngine]
        App.closeDocument(doc2.Name)
        rec["pass"] = rec["vol_at_50"] and rec["vol_at_72"] and rec["vol_at_41_after_reopen"]
    except Exception as e:
        rec["pass"] = False
        rec["error"] = "%s: %s" % (type(e).__name__, e)
    cases[tpl] = rec

result["cases"] = cases
result["working_paths"] = [k for k, v in cases.items() if v["pass"]]
result["pass"] = bool(result["working_paths"])
