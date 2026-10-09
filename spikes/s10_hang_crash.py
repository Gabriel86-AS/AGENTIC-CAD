"""S10: hang / crash behaviour. Runs in the worker; `request["case"]` picks the case.

  inproc  degenerate inputs that must fail *cleanly* (worker survives and reports):
          cyclic expression, Pad Length 0, open profile, self-intersecting profile, empty sketch,
          Pocket that removes everything.
  hang    a FeaturePython whose execute() never returns, i.e. a hang inside doc.recompute()
  crash   real SIGSEGV (faulthandler._sigsegv): the process dies with no response

The orchestrator side (spikes/drivers/s10.py) checks that hang -> timeout kill and crash ->
"no response" both come back as harness failure signals.
"""
import faulthandler

import FreeCAD as App
import Part
import Sketcher

CASE = request.get("case", "inproc")


def states(doc):
    return {o.Name: [list(o.State), o.getStatusString()] for o in doc.Objects if "Invalid" in o.State or "Error" in o.State}


def sketch_with(body, lines, name="Sketch"):
    sk = body.newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = [(body.Origin.OriginFeatures[3], "")]
    sk.MapMode = "FlatFace"
    for a, b in lines:
        sk.addGeometry(Part.LineSegment(App.Vector(*a, 0), App.Vector(*b, 0)), False)
    return sk


SQUARE = [((0, 0), (10, 0)), ((10, 0), (10, 10)), ((10, 10), (0, 10)), ((0, 10), (0, 0))]
OPEN = SQUARE[:3]
BOWTIE = [((0, 0), (10, 10)), ((10, 10), (10, 0)), ((10, 0), (0, 10)), ((0, 10), (0, 0))]


def pad_case(name, lines, length=5.0, extra=None):
    doc = App.newDocument("S10_" + name)
    body = doc.addObject("PartDesign::Body", "Body")
    sk = sketch_with(body, lines)
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sk
    pad.Length = length
    if extra:
        extra(doc, body)
    rec = {}
    try:
        rec["recompute"] = doc.recompute()
    except Exception as e:
        rec["recompute_exc"] = "%s: %s" % (type(e).__name__, e)
    rec["invalid"] = states(doc)
    rec["body_shape_null"] = body.Shape.isNull()
    rec["body_volume"] = None if body.Shape.isNull() else body.Shape.Volume
    rec["shape_valid"] = None if body.Shape.isNull() else body.Shape.isValid()
    rec["n_solids"] = None if body.Shape.isNull() else len(body.Shape.Solids)
    App.closeDocument(doc.Name)
    rec["clean_failure"] = bool(rec["invalid"]) or "recompute_exc" in rec
    return rec


def eat_all(doc, body):
    sk2 = sketch_with(body, [((-50, -50), (50, -50)), ((50, -50), (50, 50)), ((50, 50), (-50, 50)), ((-50, 50), (-50, -50))], "Big")
    pk = body.newObject("PartDesign::Pocket", "Pocket")
    pk.Profile = sk2
    pk.Type = "ThroughAll"
    pk.Midplane = True


def cyclic():
    doc = App.newDocument("S10_cyclic")
    sh = doc.addObject("Spreadsheet::Sheet", "Params")
    sh.setAlias("B1", "a")
    sh.setAlias("B2", "b")
    sh.set("B1", "=b + 1 mm")
    rec = {}
    try:
        sh.set("B2", "=a + 1 mm")
        rec["set"] = "accepted"
    except Exception as e:
        rec["set"] = "%s: %s" % (type(e).__name__, e)
    try:
        rec["recompute"] = doc.recompute()
    except Exception as e:
        rec["recompute_exc"] = "%s: %s" % (type(e).__name__, e)
    rec["invalid"] = states(doc)
    # object-level cycle: a box whose Length depends on its own Width and vice versa
    box = doc.addObject("Part::Box", "Box")
    box.setExpression("Length", "Width")
    try:
        box.setExpression("Width", "Length")
        rec["object_cycle"] = "accepted"
    except Exception as e:
        rec["object_cycle"] = "%s: %s" % (type(e).__name__, e)
    App.closeDocument(doc.Name)
    rec["clean_failure"] = rec["set"] != "accepted" or bool(rec["invalid"]) or "recompute_exc" in rec
    return rec


class Forever:
    def __init__(self, obj):
        obj.Proxy = self

    def execute(self, obj):
        while True:
            pass


if CASE == "inproc":
    cases = {
        "cyclic_expression": cyclic(),
        "pad_length_0": pad_case("len0", SQUARE, 0.0),
        "open_profile": pad_case("open", OPEN),
        "self_intersecting": pad_case("bowtie", BOWTIE),
        "empty_sketch": pad_case("empty", []),
        "pocket_removes_all": pad_case("eatall", SQUARE, extra=eat_all),
        "control_ok": pad_case("ok", SQUARE),
    }
    result["cases"] = cases
    # Degenerate inputs FreeCAD accepts without any error: our gates must catch these themselves
    result["silent"] = [k for k, v in cases.items() if k != "control_ok" and not v["clean_failure"]]
    # Pass = the worker survived every case and reported it; silent cases are findings, not failures
    result["pass"] = not cases["control_ok"]["clean_failure"]
elif CASE == "hang":
    doc = App.newDocument("S10_hang")
    f = doc.addObject("App::FeaturePython", "Forever")
    Forever(f)
    doc.recompute()
    result["pass"] = False  # unreachable if it hangs
elif CASE == "crash":
    faulthandler._sigsegv()  # real SIGSEGV; ctypes access violations are caught as OSError on Windows
    result["pass"] = False  # unreachable
