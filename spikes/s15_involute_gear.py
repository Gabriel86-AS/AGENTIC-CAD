"""S15: PartDesign InvoluteGear headless, bound to Params, padded; tooth phase; meshing pair (T2.8).

InvoluteGear is a Part::Part2DObjectPython (Mod/PartDesign/InvoluteGearFeature.py, proxy `_InvoluteGear`)
whose execute() builds the involute wire; it attaches like a sketch and a Pad takes it as Profile.
  1. teeth (unitless) + module bound to Params; pad 6 mm; outer diameter = m (z + 2)
  2. tooth phase: is a tooth or a gap on +x at angle 0? a rotationz(180 deg / teeth) AttachmentOffset flips it
  3. edit teeth / module, recompute; non-integer teeth from a Params cell (what L4 flex would do)
  4. save / reopen: proxy restored, recompute clean, edit again
  5. meshing pair z1 = 12, z2 = 30, m = 1.5, centre distance m (z1 + z2) / 2 + backlash allowance: common volume
     over a sweep of the driver with the follower at -z1/z2 (L5 with a mimic joint)
"""
import math
import os

import FreeCAD as App
import Part

import InvoluteGearFeature

OUT = request.get("out_dir") or os.getcwd()
os.makedirs(OUT, exist_ok=True)


def invalid(doc):
    return {o.Name: o.getStatusString() for o in doc.Objects if "Invalid" in o.State or "Error" in o.State}


def gear_doc(name, teeth, module, phase_gap=False):
    doc = App.newDocument(name)
    sheet = doc.addObject("Spreadsheet::Sheet", "Params")
    sheet.set("A1", "=%d" % teeth)
    sheet.setAlias("A1", "teeth")
    sheet.set("A2", "=%g mm" % module)
    sheet.setAlias("A2", "module")
    sheet.set("A3", "=6 mm")
    sheet.setAlias("A3", "thick")
    body = doc.addObject("PartDesign::Body", "Body")
    xy = [f for f in body.Origin.OriginFeatures if f.Role == "XY_Plane"][0]
    g = doc.addObject("Part::Part2DObjectPython", "gear_profile")
    InvoluteGearFeature._InvoluteGear(g)
    body.addObject(g)
    g.AttachmentSupport = [(xy, "")]
    g.MapMode = "FlatFace"
    g.setExpression("NumberOfTeeth", "Params.teeth")
    g.setExpression("Modules", "Params.module")
    if phase_gap:
        g.setExpression(".AttachmentOffset", "placement(vector(0 mm; 0 mm; 0 mm); rotationz(180 deg / Params.teeth))")
    pad = body.newObject("PartDesign::Pad", "gear_pad")
    pad.Profile = g
    pad.setExpression("Length", "Params.thick")
    doc.recompute()
    return doc, sheet, body, g, pad


def facts(doc, body, g):
    sh = body.Shape
    bb = sh.BoundBox
    return {"invalid": invalid(doc), "valid": sh.isValid(), "solids": len(sh.Solids), "volume": sh.Volume,
            "od": bb.XLength, "teeth": g.NumberOfTeeth, "module": g.Modules.Value,
            "profile_closed": g.Shape.isClosed() if g.Shape.Wires else None}


def tooth_on_x(body, module, teeth, thick=6.0):
    r_tip = module * (teeth + 2) / 2
    p = App.Vector(r_tip - 0.3 * module, 0, thick / 2)
    return body.Shape.isInside(p, 1e-6, True)


res = {}
# 1-2
doc, sheet, body, g, pad = gear_doc("g1", 20, 1.5)
res["base"] = facts(doc, body, g)
res["base"]["od_expected"] = 1.5 * 22
res["base"]["tooth_on_x"] = tooth_on_x(body, 1.5, 20)
res["base"]["types"] = [o.TypeId for o in body.Group]
docg, _, bodyg, gg, _ = gear_doc("g1gap", 20, 1.5, phase_gap=True)
res["phase_gap"] = facts(docg, bodyg, gg)
res["phase_gap"]["tooth_on_x"] = tooth_on_x(bodyg, 1.5, 20)
# 3
sheet.set("A1", "=13")
sheet.set("A2", "=2 mm")
doc.recompute()
res["edited"] = facts(doc, body, g)
res["edited"]["od_expected"] = 2 * 15
sheet.set("A1", "=15.6")
doc.recompute()
res["fractional"] = facts(doc, body, g)
sheet.set("A1", "=13")
doc.recompute()
# 4
path = os.path.join(OUT, "gear.FCStd")
doc.saveAs(path)
App.closeDocument(doc.Name)
doc = App.openDocument(path)
body, g = doc.getObject("Body"), doc.getObject("gear_profile")
res["reopen_proxy"] = type(g.Proxy).__name__ if g.Proxy else None
doc.recompute()
res["reopened"] = facts(doc, body, g)
doc.getObject("Params").set("A1", "=24")
doc.recompute()
res["reopened_edit"] = facts(doc, body, g)
App.closeDocument(doc.Name)

# 5 meshing pair
z1, z2, m = 12, 30, 1.5
da, _, ba, ga, _ = gear_doc("pa", z1, m)
res["pair_phase"] = {}
for allow in (0.0, 0.2, 0.4):
    for gap in (False, True):
        db, _, bb_, gb, _ = gear_doc("pb_%d_%d" % (int(allow * 10), gap), z2, m, phase_gap=gap)
        a0, b0 = ba.Shape.copy(), bb_.Shape.copy()
        cd = m * (z1 + z2) / 2 + allow
        worst, n = 0.0, 0
        for k in range(13):
            th = math.radians(-30 + 5 * k)
            a = a0.copy()
            a.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1), math.degrees(th))
            b = b0.copy()
            b.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1), math.degrees(-th * z1 / z2))
            b.translate(App.Vector(cd, 0, 0))
            v = a.common(b).Volume
            worst = max(worst, v)
            n += v > 1.0
        # the follower's -x side faces the driver: a gap there when (follower tooth on +x) and z2 even? record both
        res["pair_phase"]["allow%.1f_gap%s" % (allow, gap)] = {"worst_mm3": worst, "poses_over_1mm3": n,
                                                              "follower_tooth_on_minus_x": bb_.Shape.isInside(
                                                                  App.Vector(-(m * (z2 + 2) / 2 - 0.3 * m), 0, 3), 1e-6, True)}
        App.closeDocument(db.Name)
App.closeDocument(da.Name)

res["pass"] = (all(v["poses_over_1mm3"] == 0 for k, v in res["pair_phase"].items() if k.endswith("True"))
               and res["base"]["valid"] and abs(res["base"]["od"] - res["base"]["od_expected"]) < 0.2 and not res["base"]["invalid"]
               and res["base"]["tooth_on_x"] != res["phase_gap"]["tooth_on_x"]
               and abs(res["edited"]["od"] - 30) < 0.5 and res["fractional"]["teeth"] == 16 and res["reopen_proxy"] == "_InvoluteGear"
               and not res["reopened"]["invalid"] and res["reopened_edit"]["teeth"] == 24)
result.update(res)
