"""S13: PartDesign Fillet / Chamfer edge references under param edits (T2.1, TNP).

Plate (pad of a centred rect w x d, thick t) + through hole at hole_x + notch pocket at notch_x on the
top face. Fillet (or chamfer) the 4 outer top edges, picked geometrically from the tip shape at build
time. Then edit Params so that (a) sizes change, (b) the notch moves onto the outer edge (splits it:
edge count changes), recompute, and check: object state, the stored Base sub-names, and probe points
(each outer top edge midpoint, 0.2 mm in and down, must be outside the solid = still rounded).
"""
import os

import FreeCAD as App
import Part
import Sketcher

OUT = request.get("out_dir") or os.getcwd()
os.makedirs(OUT, exist_ok=True)


def invalid(doc):
    return {o.Name: o.getStatusString() for o in doc.Objects if "Invalid" in o.State or "Error" in o.State}


def rect_sketch(body, name, support, w_alias, h_alias, cx_alias=None):
    sk = body.newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = [(support, "")]
    sk.MapMode = "FlatFace"
    pts = [App.Vector(-5, -5, 0), App.Vector(5, -5, 0), App.Vector(5, 5, 0), App.Vector(-5, 5, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
    cons = [Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1) for i in range(4)]
    cons += [Sketcher.Constraint("Horizontal", 0), Sketcher.Constraint("Horizontal", 2),
             Sketcher.Constraint("Vertical", 1), Sketcher.Constraint("Vertical", 3)]
    sk.addConstraint(cons)
    if cx_alias:
        # centre x bound: corner0.x = cx - w/2, y symmetric about the h axis
        i = sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, 0, 1, -10))
        sk.renameConstraint(i, "corner_x")
        sk.setExpression(".Constraints.corner_x", "Params.%s - Params.%s / 2" % (cx_alias, w_alias))
        i = sk.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, 0, 1, -5))
        sk.renameConstraint(i, "corner_y")
        sk.setExpression(".Constraints.corner_y", "-Params.%s / 2" % h_alias)
    else:
        sk.addConstraint(Sketcher.Constraint("Symmetric", 0, 1, 2, 1, -1, 1))
    i = sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 10))
    sk.renameConstraint(i, "size_x")
    sk.setExpression(".Constraints.size_x", "Params.%s" % w_alias)
    i = sk.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, 10))
    sk.renameConstraint(i, "size_y")
    sk.setExpression(".Constraints.size_y", "Params.%s" % h_alias)
    return sk


def build(kind, tag, after_pad=False):
    doc = App.newDocument("S13_" + tag)
    sheet = doc.addObject("Spreadsheet::Sheet", "Params")
    vals = {"plate_w": "40 mm", "plate_d": "20 mm", "thick": "10 mm", "notch_x": "0 mm", "notch_w": "4 mm",
            "notch_d": "6 mm", "notch_depth": "3 mm", "radius": "2 mm"}
    for k, (a, v) in enumerate(vals.items()):
        sheet.set("B%d" % (k + 1), "=" + v)
        sheet.setAlias("B%d" % (k + 1), a)
    body = doc.addObject("PartDesign::Body", "Body")
    xy = [f for f in body.Origin.OriginFeatures if f.Role == "XY_Plane"][0]
    base = rect_sketch(body, "base", xy, "plate_w", "plate_d")
    pad = body.newObject("PartDesign::Pad", "pad")
    pad.Profile = base
    pad.setExpression("Length", "Params.thick")
    top = body.newObject("PartDesign::Plane", "top")
    top.AttachmentSupport = [(xy, "")]
    top.MapMode = "FlatFace"
    top.setExpression(".AttachmentOffset.Base.z", "Params.thick")
    nk = rect_sketch(body, "notch_sketch", top, "notch_w", "notch_d", cx_alias="notch_x")
    notch = body.newObject("PartDesign::Pocket", "notch")
    notch.Profile = nk
    notch.setExpression("Length", "Params.notch_depth")
    doc.recompute()
    ref = pad if after_pad else notch
    tip = ref.Shape
    t = 10.0
    # outer top edges: both vertices at z = t, and on the outer boundary |x| = w/2 or |y| = d/2
    picked = []
    for i, e in enumerate(tip.Edges):
        vs = [v.Point for v in e.Vertexes]
        if len(vs) == 2 and all(abs(p.z - t) < 1e-6 for p in vs):
            if all(abs(abs(p.x) - 20) < 1e-6 for p in vs) or all(abs(abs(p.y) - 10) < 1e-6 for p in vs):
                picked.append("Edge%d" % (i + 1))
    if after_pad:
        body.Tip = pad
    if kind == "fillet":
        f = body.newObject("PartDesign::Fillet", "dress")
        f.Base = (ref, picked)
        f.setExpression("Radius", "Params.radius")
    else:
        f = body.newObject("PartDesign::Chamfer", "dress")
        f.Base = (ref, picked)
        f.setExpression("Size", "Params.radius")
    if after_pad:
        body.Tip = notch
    doc.recompute()
    return doc, sheet, (body.Tip if after_pad else f), picked


def probes(doc, f):
    sh = f.Shape
    p = {a: doc.getObject("Params").get(a).Value for a in ("plate_w", "plate_d", "thick")}
    w2, d2, t = p["plate_w"] / 2, p["plate_d"] / 2, p["thick"]
    e = 0.2
    pts = {"+x": (w2 - e, 0.6 * d2, t - e), "-x": (-w2 + e, 0.6 * d2, t - e),
           "+y": (0.5 * w2, d2 - e, t - e), "-y": (0.5 * w2, -d2 + e, t - e),
           "bottom_+x": (w2 - e, 0.6 * d2, e), "vertical": (w2 - e, d2 - e, t / 2)}
    return {k: sh.isInside(App.Vector(*v), 1e-6, True) for k, v in pts.items()}


def state(doc, f):
    sh = f.Shape
    d = doc.getObject("dress")
    return {"invalid": invalid(doc), "base_subs": list(d.Base[1]), "n_solids": len(sh.Solids),
            "valid": sh.isValid(), "volume": sh.Volume, "inside": probes(doc, f)}


def run(kind, after_pad=False):
    rec = {}
    doc, sheet, f, picked = build(kind, kind + ("_after" if after_pad else ""), after_pad)
    rec["order"] = [o.Name for o in doc.getObject("Body").Group if o.isDerivedFrom("PartDesign::Feature")]
    rec["notch_base"] = doc.getObject("notch").BaseFeature.Name
    rec["picked"] = picked
    rec["has_use_all_edges"] = "UseAllEdges" in f.PropertiesList
    rec["0_build"] = state(doc, f)
    for step, changes in (("1_sizes", {"plate_w": 60, "thick": 15, "notch_w": 6}),
                          ("2_notch_on_edge", {"notch_x": 28}),       # notch 25..31 crosses x = 30: splits the +x top edge
                          ("3_notch_back", {"notch_x": 0})):
        for a, v in changes.items():
            sheet.set(sheet.getCellFromAlias(a), "=%s mm" % v)
        doc.recompute()
        rec[step] = state(doc, f)
    tipname = f.Name
    fn = os.path.join(OUT, "s13_%s%s.FCStd" % (kind, "_after" if after_pad else ""))
    doc.saveAs(fn)
    App.closeDocument(doc.Name)
    doc = App.openDocument(fn)
    sheet = doc.getObject("Params")
    sheet.set(sheet.getCellFromAlias("plate_d"), "=30 mm")
    doc.recompute()
    rec["4_reopen_edit"] = state(doc, doc.getObject(tipname))
    App.closeDocument(doc.Name)
    return rec


result["fillet"] = run("fillet")
result["chamfer"] = run("chamfer")
result["fillet_after"] = run("fillet", True)
result["chamfer_after"] = run("chamfer", True)
ok = lambda r: all(not r[s]["invalid"] and r[s]["n_solids"] == 1 and r[s]["inside"]["bottom_+x"]
                   and not any(r[s]["inside"][k] for k in ("+x", "-x", "+y", "-y"))
                   for s in ("0_build", "1_sizes", "2_notch_on_edge", "3_notch_back", "4_reopen_edit"))
result["pass"] = ok(result["fillet_after"]) and ok(result["chamfer_after"])
