"""Generator for standard-size servo cards (MG996R first). Runs inside freecadcmd as a run_script request.

request: {"card": "<path to .yaml>", "out": "<path to .FCStd>"}
Builds, in one document:
  Params (Spreadsheet)  every card params_mm entry as an alias, cells "=<v> mm" (D18)
  <Part> (App::Part)    Body + one App::LocalCoordinateSystem per card connector
  Body                  case (on a datum at -body_below) + flange + mounting holes + spline,
                        every sketch fully constrained and every dimension bound to Params
LCS placements are bound to Params too, so a param change moves connectors with the geometry.
result: gates (sketches fully constrained, one solid, no invalid objects, connectors match the card),
        volume, connectors, file paths. result["pass"] is the conjunction of the gates.
"""
import math
import os

import FreeCAD as App
import Part
import Sketcher

from components.card import connectors, freecad_expr, load_card

card = load_card(request["card"])
P = card["params_mm"]
OUT = request["out"]
os.makedirs(os.path.dirname(OUT), exist_ok=True)

doc = App.newDocument(card["id"].replace("/", "_"))
sheet = doc.addObject("Spreadsheet::Sheet", "Params")
for i, (name, value) in enumerate(P.items()):
    sheet.set("A%d" % (i + 1), name)
    sheet.set("B%d" % (i + 1), "=%s mm" % value)
    sheet.setAlias("B%d" % (i + 1), name)

part = doc.addObject("App::Part", card["id"].split("/")[-1].upper())
body = part.newObject("PartDesign::Body", "Body")
xy = body.Origin.OriginFeatures[3]  # XY_Plane


# --- sketch helpers (every dimension bound to Params) -------------------------------------
def sketch_on(support, name):
    sk = body.newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = [(support, "")]
    sk.MapMode = "FlatFace"
    return sk


def bind(sk, cons, name, expr):
    i = sk.addConstraint(cons)
    sk.renameConstraint(i, name)
    sk.setExpression(".Constraints." + name, expr)


def centred_rectangle(sk, l_alias, w_alias):
    l, w = P[l_alias], P[w_alias]
    pts = [App.Vector(-l / 2, -w / 2, 0), App.Vector(l / 2, -w / 2, 0), App.Vector(l / 2, w / 2, 0), App.Vector(-l / 2, w / 2, 0)]
    g0 = sk.GeometryCount
    for i in range(4):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
    c = [Sketcher.Constraint("Coincident", g0 + i, 2, g0 + (i + 1) % 4, 1) for i in range(4)]
    c += [Sketcher.Constraint("Horizontal", g0), Sketcher.Constraint("Horizontal", g0 + 2),
          Sketcher.Constraint("Vertical", g0 + 1), Sketcher.Constraint("Vertical", g0 + 3),
          Sketcher.Constraint("Symmetric", g0, 1, g0 + 2, 1, -1, 1)]  # centred on the sketch origin
    sk.addConstraint(c)
    bind(sk, Sketcher.Constraint("DistanceX", g0, 1, g0, 2, l), sk.Name + "_l", "Params." + l_alias)
    bind(sk, Sketcher.Constraint("DistanceY", g0 + 1, 1, g0 + 1, 2, w), sk.Name + "_w", "Params." + w_alias)


def circle(sk, x_expr, y_expr, x, y, d_alias, tag):
    """Circle centred at (x, y); x/y_expr are Params expressions, or None for 0 (point on axis)."""
    g = sk.addGeometry(Part.Circle(App.Vector(x, y, 0), App.Vector(0, 0, 1), P[d_alias] / 2), False)
    for expr, ctype, axis, v in ((x_expr, "DistanceX", -2, x), (y_expr, "DistanceY", -1, y)):
        if expr is None:  # centre on the other axis: V axis is geo -2, H axis is geo -1
            sk.addConstraint(Sketcher.Constraint("PointOnObject", g, 3, axis))
        else:
            bind(sk, Sketcher.Constraint(ctype, -1, 1, g, 3, v), "%s_%s%s" % (sk.Name, tag, ctype[-1].lower()), expr)
    return g


def datum_plane(name, z_expr):
    pl = body.newObject("PartDesign::Plane", name)
    pl.AttachmentSupport = [(xy, "")]
    pl.MapMode = "FlatFace"
    pl.setExpression(".AttachmentOffset.Base.z", z_expr)
    return pl


# --- features ------------------------------------------------------------------------------
bottom = datum_plane("CaseBottom", "-Params.body_below")
case_sk = sketch_on(bottom, "CaseSketch")
centred_rectangle(case_sk, "body_l", "body_w")
case = body.newObject("PartDesign::Pad", "Case")
case.Profile = case_sk
case.setExpression("Length", "Params.body_below + Params.case_above")

flange_sk = sketch_on(xy, "FlangeSketch")
centred_rectangle(flange_sk, "flange_l", "body_w")
flange = body.newObject("PartDesign::Pad", "Flange")
flange.Profile = flange_sk
flange.setExpression("Length", "Params.flange_t")

holes_sk = sketch_on(xy, "HolesSketch")
hx, hy = P["hole_pitch_x"] / 2, P["hole_pitch_y"] / 2
first = None
for k, (sx, sy) in enumerate(((1, 1), (-1, 1), (-1, -1), (1, -1))):
    g = circle(holes_sk, "%sParams.hole_pitch_x / 2" % ("" if sx > 0 else "-"),
               "%sParams.hole_pitch_y / 2" % ("" if sy > 0 else "-"), sx * hx, sy * hy, "hole_d", "h%d" % k)
    if first is None:
        first = g
        bind(holes_sk, Sketcher.Constraint("Diameter", g, P["hole_d"]), "hole_d", "Params.hole_d")
    else:
        holes_sk.addConstraint(Sketcher.Constraint("Equal", first, g))
holes = body.newObject("PartDesign::Pocket", "MountHoles")
holes.Profile = holes_sk
holes.Type = "ThroughAll"
holes.SideType = "Symmetric"

top = datum_plane("CaseTop", "Params.case_above")
spline_sk = sketch_on(top, "SplineSketch")
g = circle(spline_sk, "Params.shaft_x", None, P["shaft_x"], 0.0, "spline_d", "s")
bind(spline_sk, Sketcher.Constraint("Diameter", g, P["spline_d"]), "spline_d", "Params.spline_d")
spline = body.newObject("PartDesign::Pad", "Spline")
spline.Profile = spline_sk
spline.setExpression("Length", "Params.spline_top - Params.case_above")


# --- connectors: one LCS per card connector, placement bound to Params where the card names a param
def rotation(z, x):
    zv, xv = App.Vector(*z), App.Vector(*x)
    return App.Rotation(xv, zv.cross(xv), zv, "ZXY")


raw = card["connectors"]
resolved = connectors(card)
for name, c in resolved.items():
    lcs = part.newObject("App::LocalCoordinateSystem", name)
    lcs.Placement = App.Placement(App.Vector(*c["o"]), rotation(c["z"], c["x"]))
    for axis, v in zip("xyz", raw[name]["o"]):
        if isinstance(v, str):  # param name or card expression
            lcs.setExpression(".Placement.Base." + axis, freecad_expr(card, v))
    lcs.addProperty("App::PropertyString", "ConnectorLabel", "AgenticCAD").ConnectorLabel = c.get("label", "")

doc.recompute()

# --- gates --------------------------------------------------------------------------------
sketches = {}
for o in body.Group:
    if o.TypeId == "Sketcher::SketchObject":
        sketches[o.Name] = {"solve": o.solve(), "dof": o.DoF, "fully": bool(o.FullyConstrained)}
invalid = {o.Name: o.getStatusString() for o in doc.Objects if "Invalid" in o.State or "Error" in o.State}
shape = body.Shape
conn_err = {}
for name, c in resolved.items():
    plc = doc.getObject(name).getGlobalPlacement()
    want = App.Placement(App.Vector(*c["o"]), rotation(c["z"], c["x"]))
    conn_err[name] = {"pos_mm": (plc.Base - want.Base).Length,
                      "rot_deg": math.degrees(want.Rotation.inverted().multiply(plc.Rotation).Angle)}

gates = {
    "sketches_fully_constrained": all(s["solve"] == 0 and s["fully"] for s in sketches.values()),
    "one_solid": (not shape.isNull()) and len(shape.Solids) == 1 and shape.Volume > 0,
    "no_invalid_objects": not invalid,
    "connectors_match_card": all(e["pos_mm"] < 1e-6 and e["rot_deg"] < 1e-6 for e in conn_err.values()),
}
doc.saveAs(OUT)

result.update({
    "card": card["id"], "fcstd": OUT, "gates": gates, "sketches": sketches, "invalid": invalid,
    "volume_mm3": shape.Volume if not shape.isNull() else None,
    "bound_box": [shape.BoundBox.XMin, shape.BoundBox.YMin, shape.BoundBox.ZMin,
                  shape.BoundBox.XMax, shape.BoundBox.YMax, shape.BoundBox.ZMax] if not shape.isNull() else None,
    "connectors": {n: {"o": list(doc.getObject(n).getGlobalPlacement().Base), "err": conn_err[n]} for n in resolved},
    "pass": all(gates.values()),
})
App.closeDocument(doc.Name)
