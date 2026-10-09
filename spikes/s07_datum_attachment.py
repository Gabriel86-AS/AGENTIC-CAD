"""S7: datum attached to the Body origin, offset bound to Params, sketch on the datum.

Per datum type (PartDesign::Plane, PartDesign::CoordinateSystem): attach to XY_Plane
(MapMode FlatFace), bind AttachmentOffset z to Params.h, sketch a square on the datum, pad 5 mm.
Change Params.h -> recompute -> the pad's ZMin must follow; no invalid objects. Also after reopen.
"""
import os

import FreeCAD as App
import Part
import Sketcher

OUT = request.get("out_dir") or os.getcwd()
os.makedirs(OUT, exist_ok=True)
EXPR_PATHS = [".AttachmentOffset.Base.z", "AttachmentOffset.Base.z"]


def invalid(doc):
    return [o.Name for o in doc.Objects if "Invalid" in o.State or "Error" in o.State]


def build(datum_type, expr_path, tag):
    doc = App.newDocument("S7_" + tag)
    sheet = doc.addObject("Spreadsheet::Sheet", "Params")
    sheet.set("B1", "12 mm")
    sheet.setAlias("B1", "datum_h")
    body = doc.addObject("PartDesign::Body", "Body")
    datum = body.newObject(datum_type, "Datum")
    datum.AttachmentSupport = [(body.Origin.OriginFeatures[3], "")]  # XY_Plane
    datum.MapMode = "FlatFace"
    datum.setExpression(expr_path, "Params.datum_h")
    sk = body.newObject("Sketcher::SketchObject", "Sketch")
    sk.AttachmentSupport = [(datum, "")]
    sk.MapMode = "FlatFace"
    pts = [App.Vector(0, 0, 0), App.Vector(10, 0, 0), App.Vector(10, 10, 0), App.Vector(0, 10, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sk
    pad.Length = 5.0
    doc.recompute()
    return doc, sheet, pad


def zmin(pad):
    return pad.Shape.BoundBox.ZMin


def run(datum_type, expr_path, tag):
    rec = {}
    doc, sheet, pad = build(datum_type, expr_path, tag)
    rec["zmin_at_12"] = zmin(pad)
    rec["invalid_at_12"] = invalid(doc)
    sheet.set("B1", "30 mm")
    doc.recompute()
    rec["zmin_at_30"] = zmin(pad)
    rec["invalid_at_30"] = invalid(doc)
    fn = os.path.join(OUT, "s07_%s.FCStd" % tag)
    doc.saveAs(fn)
    App.closeDocument(doc.Name)
    doc2 = App.openDocument(fn)
    doc2.getObject("Params").set("B1", "=-7 mm")
    doc2.recompute()
    rec["zmin_at_-7_after_reopen"] = zmin(doc2.getObject("Pad"))
    rec["invalid_after_reopen"] = invalid(doc2)
    App.closeDocument(doc2.Name)
    rec["pass"] = (abs(rec["zmin_at_12"] - 12) < 1e-6 and abs(rec["zmin_at_30"] - 30) < 1e-6
                   and abs(rec["zmin_at_-7_after_reopen"] + 7) < 1e-6
                   and not (rec["invalid_at_12"] or rec["invalid_at_30"] or rec["invalid_after_reopen"]))
    return rec


cases = {}
for dt in ("PartDesign::Plane", "PartDesign::CoordinateSystem"):
    for i, ep in enumerate(EXPR_PATHS):
        key = "%s | %s" % (dt, ep)
        try:
            cases[key] = run(dt, ep, "%s_%d" % (dt.split("::")[1], i))
        except Exception as e:
            cases[key] = {"pass": False, "error": "%s: %s" % (type(e).__name__, e)}
result["cases"] = cases
result["working"] = [k for k, v in cases.items() if v["pass"]]
result["pass"] = any(k.startswith("PartDesign::Plane") for k in result["working"])

# Alias names that collide with unit symbols are rejected by setAlias
_d = App.newDocument("S7_alias")
_s = _d.addObject("Spreadsheet::Sheet", "Params")
alias_probe = {}
for i, name in enumerate(["h", "m", "mm", "s", "g", "deg", "len", "L", "width", "x", "a1", "upper_arm_len"]):
    try:
        _s.setAlias("A%d" % (i + 1), name)
        alias_probe[name] = "ok"
    except Exception as e:
        alias_probe[name] = "%s: %s" % (type(e).__name__, e)
App.closeDocument(_d.Name)
result["alias_probe"] = alias_probe
