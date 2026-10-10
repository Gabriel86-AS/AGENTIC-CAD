"""S14: PartDesign Mirrored / LinearPattern / PolarPattern / Thickness headless, bound to Params (T2.1).

Base plate pad + a boss pad off-centre. Then, one document per feature:
  mirror:  Mirrored of the boss about the YZ origin plane
  linear:  LinearPattern of the boss along the X axis, Length and Occurrences bound to Params
  polar:   PolarPattern of the boss about the Z axis, Angle and Occurrences bound to Params
  shell:   Thickness of the plate removing its top face (picked from the pad's tool shape), Value bound
Transformed features made by body.newObject do not become the Tip: set body.Tip.
Edit the Params (sizes + occurrences), recompute, save/reopen, edit again. Record state, volume,
property names (1.1 API), and where AddSubShape lives (part frame or sketch frame).
"""
import os

import FreeCAD as App
import Part
import Sketcher

OUT = request.get("out_dir") or os.getcwd()
os.makedirs(OUT, exist_ok=True)


def invalid(doc):
    return {o.Name: o.getStatusString() for o in doc.Objects if "Invalid" in o.State or "Error" in o.State}


def bind(sk, cons, name, expr):
    i = sk.addConstraint(cons)
    sk.renameConstraint(i, name)
    sk.setExpression(".Constraints." + name, expr)


def rect(body, name, support):
    sk = body.newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = [(support, "")]
    sk.MapMode = "FlatFace"
    pts = [App.Vector(-5, -5, 0), App.Vector(5, -5, 0), App.Vector(5, 5, 0), App.Vector(-5, 5, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
    sk.addConstraint([Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1) for i in range(4)]
                     + [Sketcher.Constraint("Horizontal", 0), Sketcher.Constraint("Horizontal", 2),
                        Sketcher.Constraint("Vertical", 1), Sketcher.Constraint("Vertical", 3),
                        Sketcher.Constraint("Symmetric", 0, 1, 2, 1, -1, 1)])
    bind(sk, Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 10), "size_x", "Params.plate_w")
    bind(sk, Sketcher.Constraint("DistanceY", 1, 1, 1, 2, 10), "size_y", "Params.plate_d")
    return sk


def circle(body, name, support):
    sk = body.newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = [(support, "")]
    sk.MapMode = "FlatFace"
    g = sk.addGeometry(Part.Circle(App.Vector(10, 5, 0), App.Vector(0, 0, 1), 3), False)
    bind(sk, Sketcher.Constraint("DistanceX", -1, 1, g, 3, 10), "cx", "Params.boss_x")
    bind(sk, Sketcher.Constraint("DistanceY", -1, 1, g, 3, 5), "cy", "Params.boss_y")
    bind(sk, Sketcher.Constraint("Diameter", g, 6), "dia", "Params.boss_d")
    return sk


def build(tag):
    doc = App.newDocument("S14_" + tag)
    sheet = doc.addObject("Spreadsheet::Sheet", "Params")
    vals = {"plate_w": "=80 mm", "plate_d": "=40 mm", "thick": "=10 mm", "boss_x": "=20 mm", "boss_y": "=10 mm",
            "boss_d": "=6 mm", "boss_h": "=5 mm", "spacing": "=30 mm", "count": "=3", "sweep": "=180 deg",
            "wall": "=2 mm"}
    for k, (a, v) in enumerate(vals.items()):
        sheet.set("B%d" % (k + 1), v)
        sheet.setAlias("B%d" % (k + 1), a)
    body = doc.addObject("PartDesign::Body", "Body")
    org = {f.Role: f for f in body.Origin.OriginFeatures}
    pad = body.newObject("PartDesign::Pad", "plate")
    pad.Profile = rect(body, "plate_sketch", org["XY_Plane"])
    pad.setExpression("Length", "Params.thick")
    top = body.newObject("PartDesign::Plane", "top")
    top.AttachmentSupport = [(org["XY_Plane"], "")]
    top.MapMode = "FlatFace"
    top.setExpression(".AttachmentOffset.Base.z", "Params.thick")
    boss = body.newObject("PartDesign::Pad", "boss")
    boss.Profile = circle(body, "boss_sketch", top)
    boss.setExpression("Length", "Params.boss_h")
    doc.recompute()
    return doc, sheet, body, org, pad, boss


def feature(tag):
    doc, sheet, body, org, pad, boss = build(tag)
    rec = {}
    if tag == "linear":
        sheet.set(sheet.getCellFromAlias("boss_x"), "=-30 mm")
        sheet.set(sheet.getCellFromAlias("spacing"), "=20 mm")
        doc.recompute()
    if tag == "mirror":
        f = body.newObject("PartDesign::Mirrored", "feat")
        f.Originals = [boss]
        f.MirrorPlane = (org["YZ_Plane"], [""])
        edits = [{"boss_x": 25}]
        body.Tip = f  # a Transformed feature made by newObject does not become the Tip (probed)
    elif tag == "linear":
        f = body.newObject("PartDesign::LinearPattern", "feat")
        f.Originals = [boss]
        f.Direction = (org["X_Axis"], [""])
        rec["props"] = [p for p in f.PropertiesList if p in ("Length", "Offset", "Occurrences", "Mode", "Reversed")]
        f.setExpression("Length", "Params.spacing * (Params.count - 1)")
        f.setExpression("Occurrences", "Params.count")
        edits = [{"count": 2}, {"spacing": 15, "count": 4}]
        body.Tip = f
    elif tag == "polar":
        f = body.newObject("PartDesign::PolarPattern", "feat")
        f.Originals = [boss]
        f.Axis = (org["Z_Axis"], [""])
        rec["props"] = [p for p in f.PropertiesList if p in ("Angle", "Offset", "Occurrences", "Mode", "Reversed")]
        f.setExpression("Angle", "Params.sweep")
        f.setExpression("Occurrences", "Params.count")
        edits = [{"count": 4, "sweep": 360}, {"boss_x": 15}]
        body.Tip = f
    else:  # shell: remove the plate's bottom face (from the pad, inserted before the boss)
        tool = pad.AddSubShape
        rec["addsub_bbox"] = [tool.BoundBox.ZMin, tool.BoundBox.ZMax]
        rec["addsub_placement"] = str(tool.Placement)
        faces = ["Face%d" % (i + 1) for i, fc in enumerate(pad.Shape.Faces)
                 if abs(fc.BoundBox.ZMin) < 1e-6 and abs(fc.BoundBox.ZMax) < 1e-6]
        rec["faces"] = faces
        body.Tip = pad
        f = body.newObject("PartDesign::Thickness", "feat")
        f.Base = (pad, faces)
        f.setExpression("Value", "Params.wall")
        rec["props"] = [p for p in f.PropertiesList if p in ("Value", "Reversed", "Mode", "Join", "Intersection")]
        body.Tip = boss
        edits = [{"wall": 3}, {"plate_w": 60, "thick": 15}]
    doc.recompute()
    tip = body.Tip
    rec["order"] = [o.Name for o in body.Group if o.isDerivedFrom("PartDesign::Feature")]
    rec["0"] = {"invalid": invalid(doc), "volume": tip.Shape.Volume, "solids": len(tip.Shape.Solids)}
    for k, ch in enumerate(edits):
        for a, v in ch.items():
            unit = "" if a == "count" else (" deg" if a == "sweep" else " mm")
            sheet.set(sheet.getCellFromAlias(a), "=%s%s" % (v, unit))
        doc.recompute()
        rec[str(k + 1)] = {"invalid": invalid(doc), "volume": tip.Shape.Volume, "solids": len(tip.Shape.Solids),
                           "occ": getattr(f, "Occurrences", None)}
    fn = os.path.join(OUT, "s14_%s.FCStd" % tag)
    name = tip.Name
    doc.saveAs(fn)
    App.closeDocument(doc.Name)
    doc = App.openDocument(fn)
    sheet = doc.getObject("Params")
    sheet.set(sheet.getCellFromAlias("boss_d"), "=8 mm")
    doc.recompute()
    t = doc.getObject(name)
    rec["reopen"] = {"invalid": invalid(doc), "volume": t.Shape.Volume, "solids": len(t.Shape.Solids)}
    App.closeDocument(doc.Name)
    return rec


for tag in ("mirror", "linear", "polar", "shell"):
    try:
        result[tag] = feature(tag)
    except Exception as e:
        import traceback
        result[tag] = {"error": traceback.format_exc()}
result["pass"] = all("error" not in result[t] and all(not result[t][k]["invalid"] for k in result[t]
                                                       if isinstance(result[t][k], dict) and "invalid" in result[t][k])
                     for t in ("mirror", "linear", "polar", "shell"))
