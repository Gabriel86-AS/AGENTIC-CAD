"""Worker ops for part programs (P1 §4.3): compile_part and probe_part. Runs inside freecadcmd.

compile_part request:
    program     path of parts/<name>.py          part        expected Part name (optional)
    params      plan params {name: {value, min, max, unit}}
    components  {component_id: card_id}           profile     print profile name (optional)
    out         .FCStd path to save
  -> result = sdk Part.report() (gates L1/L2/L3-local, sketches, connectors, params, volume, bbox),
     artifacts.fcstd; signals carry `parts/<name>.py:<line>` locations.

probe_part request:
    fcstd       a compiled part                   snippet     Python using `probe` (and `math`)
  -> result = value of the snippet's last expression (or its `result` variable), JSON-safe.
     The document is opened, measured and closed without saving: probes are read-only.
"""
import ast
import math
import os
import traceback

import FreeCAD as App
import Part as OCC

import sdk
from sdk import Part, PlanParams, SDKError
from sdk.errors import signal


def _program_line(exc, display):
    """Deepest traceback line inside the part program, as 'parts/x.py:12'."""
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == display]
    return "%s:%d" % (display, frames[-1].lineno) if frames else display


def _located(sig, where):
    loc = sig.get("location") or ""
    return dict(sig, location=where + (" (%s)" % loc if loc and loc != where else ""))


def compile_part(req, res):
    path = req["program"]
    display = req.get("display") or "parts/%s" % os.path.basename(path)
    sdk.configure(profile=req.get("profile"), components=req.get("components") or {})
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    ns = {"__name__": "__agenticcad_part__"}
    try:
        exec(compile(source, display, "exec"), ns)
        part = ns["build"](PlanParams(req.get("params") or {}))
    except SDKError as e:
        res["signals"].append(_located(e.signal, _program_line(e, display)))
        return
    except Exception as e:
        where = _program_line(e, display)
        res["signals"].append(signal("failure", "L1", "%s: %s" % (type(e).__name__, e), where,
                                     "fix the part program at %s" % where))
        return
    if not isinstance(part, Part):
        res["signals"].append(signal("failure", "L1", "build(P) returned %s, not a Part" % type(part).__name__,
                                     display, "end build(P) with `return part`"))
        return
    if req.get("part") and part.name != req["part"]:
        res["signals"].append(signal("failure", "L1", "Part is named %r; the plan calls it %r" % (part.name, req["part"]),
                                     display, "use Part(%r, ...)" % req["part"]))
    rep = part.report()
    res["signals"].extend(rep.pop("signals"))
    if req.get("out"):
        os.makedirs(os.path.dirname(req["out"]), exist_ok=True)
        part.save(req["out"])
        res["artifacts"]["fcstd"] = req["out"]
    res["metrics"] = {"volume_mm3": rep["volume_mm3"], "n_solids": rep["n_solids"],
                      "n_sketches": len(rep["sketches"]), "max_dof": max([s["dof"] for s in rep["sketches"].values()] or [0]),
                      "n_params": len(rep["params"])}
    res["result"] = rep
    res["ok"] = not any(s["level"] == "failure" for s in res["signals"])
    part.close()


# ---- probe ----------------------------------------------------------------------------------
def _vec(v):
    return [round(float(c), 9) for c in v]


class Probe:
    """Read-only measurements of a compiled part; every method returns plain JSON values (mm, deg)."""

    def __init__(self, doc):
        self._doc = doc
        self._body = next(o for o in doc.Objects if o.TypeId == "PartDesign::Body")
        self._shape = self._body.Shape
        self._sheet = doc.getObject("Params")

    def help(self):
        return sorted(n for n in dir(self) if not n.startswith("_"))

    def volume(self):
        return self._shape.Volume

    def area(self):
        return self._shape.Area

    def bbox(self):
        b = self._shape.BoundBox
        return {"min": _vec((b.XMin, b.YMin, b.ZMin)), "max": _vec((b.XMax, b.YMax, b.ZMax)),
                "size": _vec((b.XLength, b.YLength, b.ZLength))}

    def com(self):
        return _vec(self._shape.Solids[0].CenterOfMass) if self._shape.Solids else None

    def connectors(self):
        out = {}
        for o in self._doc.Objects:
            if o.TypeId == "App::LocalCoordinateSystem" and hasattr(o, "ConnectorLabel"):
                r = o.Placement.Rotation
                out[o.Name] = {"o": _vec(o.Placement.Base), "z": _vec(r.multVec(App.Vector(0, 0, 1))),
                               "x": _vec(r.multVec(App.Vector(1, 0, 0))), "label": o.ConnectorLabel}
        return out

    def connector(self, name):
        c = self.connectors()
        if name not in c:
            raise KeyError("no connector %r; connectors: %s" % (name, sorted(c)))
        return c[name]

    def params(self):
        text = lambda cell: self._sheet.getContents(cell).lstrip("'")  # string cells carry a leading '
        out = {}
        for row in range(2, 10000):
            alias = text("A%d" % row)
            if not alias:
                break
            v = self._sheet.get("B%d" % row)
            out[alias] = {"value": float(v.Value) if hasattr(v, "Value") else v, "source": text("E%d" % row)}
        return out

    def param(self, name):
        p = self.params()
        if name not in p:
            raise KeyError("no param %r" % name)
        return p[name]["value"]

    def sketches(self):
        return {o.Name: {"dof": o.DoF, "fully": bool(o.FullyConstrained), "geometry": o.GeometryCount}
                for o in self._body.Group if o.TypeId == "Sketcher::SketchObject"}

    def features(self):
        return [{"name": o.Name, "type": o.TypeId.split("::")[-1], "state": list(o.State)}
                for o in self._body.Group if o.TypeId.startswith("PartDesign::") and o.TypeId != "PartDesign::Plane"]

    def inside(self, p):
        return bool(self._shape.isInside(App.Vector(*p), 1e-6, True))

    def distance(self, p):
        """Distance from point p to the part's surface (0 on the surface; inside points too)."""
        return self._shape.Shells[0].distToShape(OCC.Vertex(App.Vector(*p)))[0] if self._shape.Shells else None

    def segments(self, p0, p1):
        """Parts of the segment p0 -> p1 inside material, as [[t0, t1], ...] in mm from p0."""
        a, b = App.Vector(*p0), App.Vector(*p1)
        edge = OCC.makeLine(a, b)
        common = self._shape.common(edge)
        out = []
        for e in common.Edges:
            t = sorted(((v.Point - a).Length for v in e.Vertexes))
            out.append([round(t[0], 9), round(t[-1], 9)])
        return sorted(out)

    def thickness(self, p, direction, reach=1000.0):
        """Material thickness through point p (inside the part) along `direction`, both ways."""
        d = App.Vector(*direction)
        if d.Length < 1e-12:
            raise ValueError("direction is a zero vector")
        d.normalize()
        P = App.Vector(*p)
        for t0, t1 in self.segments(list(P - d * reach), list(P + d * reach)):
            if t0 - 1e-6 <= reach <= t1 + 1e-6:
                return t1 - t0
        return 0.0


def _jsonable(v):
    if isinstance(v, App.Vector):
        return _vec(v)
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    return repr(v)


def probe_part(req, res):
    doc = App.openDocument(req["fcstd"])
    try:
        doc.recompute()
        ns = {"probe": Probe(doc), "math": math, "result": None}
        tree = ast.parse(req["snippet"], filename="probe")
        last = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) else None
        try:
            exec(compile(tree, "probe", "exec"), ns)
            value = eval(compile(ast.Expression(last.value), "probe", "eval"), ns) if last else ns.get("result")
        except Exception as e:
            frames = [f for f in traceback.extract_tb(e.__traceback__) if f.filename == "probe"]
            where = "probe:%d" % frames[-1].lineno if frames else "probe"
            res["signals"].append(signal("failure", "probe", "%s: %s" % (type(e).__name__, e), where,
                                         "call probe.help() for the available measurements"))
            return
        res["result"] = _jsonable(value)
        res["ok"] = True
    finally:
        App.closeDocument(doc.Name)
