"""Part: one printed part = one FreeCAD document (plan §3.3, P1 §4.2).

    Params   Spreadsheet::Sheet  columns name | value | min | max | source; values "=<v> <unit>" (D18)
    <name>   App::Part           holds Body + one App::LocalCoordinateSystem per connector
    Body     PartDesign::Body    datums, sketches, pads, pockets; never attached to faces (D19)

Every length the program passes (number, plan param or Q expression) ends up as an expression of a
Params alias; literals are promoted to aliases named after the call (`source = literal`).
Frames (`frame=` datums, connectors, mount frames) are relative to the Body origin = part frame.
Origin planes (probed 2026-10-09): XY sketch (x, y) -> (X, Y), normal +Z; XZ -> (X, Z), normal -Y;
YZ -> (Y, Z), normal +X. Pad grows along the sketch normal, Pocket cuts against it.
"""
import math

import FreeCAD as App

from components.card import card_path, load_card
from profiles import load_profile
from schemas.names import name_error
from sdk.errors import SDKError, signal
from sdk.expr import Q, fmt, is_num
from sdk.sketch import Sketch

PLANES = ("XY", "XZ", "YZ")
DIMENSIONAL = {"Distance", "DistanceX", "DistanceY", "Radius", "Diameter", "Angle"}
RESERVED = {"params", "body"}


class _Context:
    """Set by the worker before a part program runs (plan excerpt, profile); tests set it directly."""

    def __init__(self):
        self.profile = None
        self.components = {}  # component id -> card id ("s_yaw" -> "servo/mg996r")

    def get_profile(self):
        if self.profile is None:
            self.profile = load_profile("generic_fdm")
        return self.profile


context = _Context()


def configure(profile=None, components=None):
    """profile: dict or profile name; components: {component_id: card_id} from the plan."""
    context.profile = load_profile(profile) if isinstance(profile, str) else profile
    context.components = dict(components or {})


def _check_name(name, what):
    err = name_error(name)
    if err:
        raise SDKError("sdk", "%s name %s" % (what, err), action="rename it")
    return name


def _unit(v, what):
    v = App.Vector(*[float(c) for c in v])
    if v.Length < 1e-9:
        raise SDKError("sdk", "%s is a zero vector" % what)
    return v.normalize()


def _rotation(z, x):
    z, x = _unit(z, "z"), _unit(x, "x")
    if abs(z.dot(x)) > 1e-6:
        raise SDKError("sdk", "z %s and x %s are not orthogonal" % (tuple(z), tuple(x)),
                       action="pass x perpendicular to z")
    return App.Rotation(x, z.cross(x), z, "ZXY")


class ParamSet:
    """Return value of part.params(): attribute access to the Qs it created."""

    def __init__(self, qs):
        self.__dict__.update(qs)

    def __repr__(self):
        return "ParamSet(%s)" % ", ".join("%s=%s" % kv for kv in self.__dict__.items())


class Datum:
    def __init__(self, name, obj, plane=None):
        self.name = name
        self.obj = obj
        self.plane = plane  # origin plane name, or None for frame datums


class Part:
    def __init__(self, name, material="PETG"):
        _check_name(name, "part")
        if name in RESERVED:
            raise SDKError("sdk", "part name %r is reserved" % name)
        if material not in ("PETG", "PLA"):
            raise SDKError("sdk", "material must be PETG or PLA, got %r" % (material,))
        self.name = name
        self.material = material
        self.profile = context.get_profile()
        self.doc = App.newDocument(name)
        self.sheet = self.doc.addObject("Spreadsheet::Sheet", "Params")
        for col, head in zip("ABCDE", ("name", "value", "min", "max", "source")):
            self.sheet.set(col + "1", head)
        self.container = self.doc.addObject("App::Part", name)
        self.container.addProperty("App::PropertyString", "PrintMaterial", "AgenticCAD").PrintMaterial = material
        self.body = self.container.newObject("PartDesign::Body", "Body")
        self.params_info = {}   # alias -> {value, unit, min, max, source}
        self.sketches = {}
        self.datums = {}
        self.connectors = {}    # name -> {"o": (Q|0)x3, "z", "x", "label", "obj"}
        self.signals = []
        self._names = {name, "Params", "Body"}
        self._n = {}

    # ---- naming ----------------------------------------------------------------------------
    def _new_name(self, name, kind):
        if name is None:
            k = self._n.get(kind, 0)
            while "%s%d" % (kind, k) in self._names:
                k += 1
            self._n[kind] = k + 1
            name = "%s%d" % (kind, k)
        _check_name(name, kind)
        if name in self._names:
            raise SDKError("sdk", "name %r already used in part %s" % (name, self.name), action="pick a unique name")
        self._names.add(name)
        return name

    def _origin(self, role):
        for f in self.body.Origin.OriginFeatures:
            if f.Role == role:
                return f
        raise SDKError("sdk", "body origin has no %s" % role)

    # ---- params ----------------------------------------------------------------------------
    def _add_param(self, alias, v, unit="mm", lo=None, hi=None, source=""):
        err = name_error(alias)
        if err:
            raise SDKError("sdk", "param " + err, action="rename the param")
        if alias in self.params_info:
            raise SDKError("sdk", "param %r already defined (%s)" % (alias, self.params_info[alias]["source"]),
                           action="use a different name")
        row = len(self.params_info) + 2
        u = "" if unit in ("", "-") else " " + unit
        val = v.value if isinstance(v, Q) else float(v)
        self.sheet.set("A%d" % row, alias)
        self.sheet.set("B%d" % row, "=" + v.cell() if isinstance(v, Q) else "=%s%s" % (fmt(val), u))
        if lo is not None:
            self.sheet.set("C%d" % row, fmt(lo))
            self.sheet.set("D%d" % row, fmt(hi))
        self.sheet.set("E%d" % row, source)
        try:
            self.sheet.setAlias("B%d" % row, alias)
        except ValueError as e:
            raise SDKError("sdk", "FreeCAD rejected param alias %r: %s" % (alias, e), action="rename the param")
        self.params_info[alias] = {"value": val, "unit": unit, "min": lo, "max": hi, "source": source}
        return Q(val, "Params." + alias, unit)

    def _ensure_plan(self, q: Q):
        for name, p in q.plan.items():
            info = self.params_info.get(name)
            if info is None:
                self._add_param(name, p["value"], p["unit"], p["min"], p["max"], source="plan")
            elif info["source"] != "plan":
                raise SDKError("sdk", "part param %r shadows the plan param of the same name" % name,
                               action="rename the part param")

    def _unique_alias(self, base):
        alias, k = base, 2
        while alias in self.params_info:
            alias, k = "%s_%d" % (base, k), k + 1
        return alias

    def _q(self, v, hint, unit="mm", source="literal"):
        """Q for any length argument: plan Qs are registered, literals promoted to an alias."""
        if isinstance(v, Q):
            self._ensure_plan(v)
            return v
        if is_num(v):
            return self._add_param(self._unique_alias(hint), v, unit, source=source)
        raise SDKError("sdk", "%s: expected a number or a param, got %r" % (hint, v))

    def params(self, **kw):
        """Define part params. Values: number (mm), a plan param / Q expression, or
        {"value", "min", "max", "unit"}. Returns an object with one Q per name."""
        out = {}
        for alias, v in kw.items():
            if isinstance(v, dict):
                out[alias] = self._add_param(alias, v["value"], v.get("unit", "mm"), v.get("min"), v.get("max"), "part")
            elif isinstance(v, Q):
                self._ensure_plan(v)
                out[alias] = self._add_param(alias, v, v.unit, source="part")
            elif is_num(v):
                out[alias] = self._add_param(alias, v, source="part")
            else:
                raise SDKError("sdk", "param %s: expected a number, a param or a dict, got %r" % (alias, v))
        return ParamSet(out)

    def _profile_q(self, alias, mm):
        """Profile value (fit allowance, screw hole) as a sheet alias, written once."""
        if alias not in self.params_info:
            self._add_param(alias, mm, source="profile:%s" % self.profile.get("id", "?"))
        return Q(self.params_info[alias]["value"], "Params." + alias)

    # ---- datums ----------------------------------------------------------------------------
    def _frame_datum(self, name, origin, rot):
        """PartDesign::Plane on the XY origin plane with AttachmentOffset = (origin, rot); origin parts bound."""
        pl = self.body.newObject("PartDesign::Plane", name)
        pl.AttachmentSupport = [(self._origin("XY_Plane"), "")]
        pl.MapMode = "FlatFace"
        pl.AttachmentOffset = App.Placement(App.Vector(*[float(c) for c in origin]), rot)
        for axis, c in zip("xyz", origin):
            if isinstance(c, Q):
                pl.setExpression(".AttachmentOffset.Base." + axis, c.expr)
        return pl

    def _point(self, p, hint):
        return tuple(0 if (is_num(c) and c == 0) else self._q(c, "%s_%s" % (hint, a)) for a, c in zip("xyz", p))

    def datum(self, name=None, plane="XY", offset=0, origin=None, z=None, x=None):
        """Datum plane: `plane` + `offset` along its normal, or a full frame (`origin`, `z`, `x`).
        A sketch on it has its x along the datum x and its normal along the datum z."""
        name = self._new_name(name, "datum")
        if origin is not None or z is not None or x is not None:
            o = self._point(origin or (0, 0, 0), name + "_o")
            pl = self._frame_datum(name, o, _rotation(z or (0, 0, 1), x or (1, 0, 0)))
            d = Datum(name, pl)
        else:
            if plane not in PLANES:
                raise SDKError("sdk", "plane must be one of %s, got %r" % (PLANES, plane))
            pl = self.body.newObject("PartDesign::Plane", name)
            pl.AttachmentSupport = [(self._origin(plane + "_Plane"), "")]
            pl.MapMode = "FlatFace"
            if not (is_num(offset) and offset == 0):
                q = self._q(offset, name + "_offset")
                pl.AttachmentOffset = App.Placement(App.Vector(0, 0, q.value), App.Rotation())
                pl.setExpression(".AttachmentOffset.Base.z", q.expr)
            d = Datum(name, pl, plane)
        self.datums[name] = d
        return d

    # ---- sketches and features ---------------------------------------------------------------
    def sketch(self, name=None, on="XY"):
        """Sketch on an origin plane ("XY" | "XZ" | "YZ") or a Datum; never on a face."""
        name = self._new_name(name, "sketch")
        if isinstance(on, Datum):
            support = on.obj
        elif on in PLANES:
            support = self._origin(on + "_Plane")
        else:
            raise SDKError("sdk", "sketch %s: `on` must be a Datum or one of %s, got %r" % (name, PLANES, on),
                           action="create a datum with part.datum() and sketch on it")
        sk = self.body.newObject("Sketcher::SketchObject", name)
        sk.AttachmentSupport = [(support, "")]
        sk.MapMode = "FlatFace"
        s = Sketch(self, sk, name)
        self.sketches[name] = s
        return s

    def _profile_of(self, sketch, call):
        if not isinstance(sketch, Sketch) or sketch.part is not self:
            raise SDKError("sdk", "%s: pass a sketch made by this part's part.sketch()" % call)
        if sketch.obj.GeometryCount == 0:
            raise SDKError("sdk", "%s: sketch %s is empty" % (call, sketch.name), location=sketch.name)
        return sketch.obj

    def pad(self, sketch, length, reverse=False, symmetric=False, name=None):
        """Extrude along the sketch normal (or against it with reverse, or both ways with symmetric)."""
        prof = self._profile_of(sketch, "pad")
        name = self._new_name(name or sketch.name + "_pad", "pad")
        q = self._q(length, name + "_length")
        if q.value <= 0:
            raise SDKError("sdk", "pad %s: length must be > 0, got %g" % (name, q.value), location=name)
        f = self.body.newObject("PartDesign::Pad", name)
        f.Profile = prof
        f.Length = q.value
        f.setExpression("Length", q.expr)
        f.Reversed = bool(reverse)
        f.Midplane = bool(symmetric)
        return f

    def pocket(self, sketch, depth=None, through=False, symmetric=False, reverse=False, name=None):
        """Cut against the sketch normal by `depth`, or `through=True` (through all; both ways if symmetric)."""
        prof = self._profile_of(sketch, "pocket")
        name = self._new_name(name or sketch.name + "_pocket", "pocket")
        if through == (depth is not None):
            raise SDKError("sdk", "pocket %s: pass exactly one of depth= or through=True" % name, location=name)
        f = self.body.newObject("PartDesign::Pocket", name)
        f.Profile = prof
        if through:
            f.Type = "ThroughAll"
        else:
            q = self._q(depth, name + "_depth")
            if q.value <= 0:
                raise SDKError("sdk", "pocket %s: depth must be > 0, got %g" % (name, q.value), location=name)
            f.Length = q.value
            f.setExpression("Length", q.expr)
        f.Reversed = bool(reverse)
        f.Midplane = bool(symmetric)
        return f

    def hole_d(self, d=None, fit="clearance", horizontal=False):
        """Printed hole diameter as a Q: `fit` from the profile `fits` (d + allowance) or a screw fit
        like "clearance_m3" / "tap_m3" (d ignored). horizontal adds the profile's extra allowance."""
        kind, _, thread = fit.partition("_")
        if thread:
            screws = {k.lower().replace(".", "_"): v for k, v in self.profile["screws"].items()}
            if thread not in screws or kind not in screws[thread]:
                raise SDKError("sdk", "unknown screw fit %r; screws: %s, kinds: clearance, tap"
                               % (fit, sorted(screws)), action="use e.g. fit='clearance_m3'")
            q = self._profile_q("%s_%s" % (kind, thread), screws[thread][kind])
        else:
            if fit not in self.profile["fits"]:
                raise SDKError("sdk", "unknown fit %r; profile fits: %s or a screw fit like tap_m3"
                               % (fit, sorted(self.profile["fits"])))
            if d is None:
                raise SDKError("sdk", "fit %r needs a nominal diameter d" % fit)
            q = self._q(d, "hole_nominal_d") + self._profile_q("fit_" + fit, self.profile["fits"][fit])
        if horizontal:
            q = q + self._profile_q("horizontal_hole_extra", self.profile["horizontal_hole_extra_mm"])
        return q

    def hole(self, on, at, d=None, fit="clearance", depth=None, horizontal=False, name=None):
        """Round hole(s) at `at` = (u, v) or a list of points in the sketch frame of `on`; diameter from
        the print profile (see hole_d). Through all along the normal unless `depth` is given."""
        name = self._new_name(name, "hole")
        dq = self.hole_d(d, fit, horizontal)
        pts = [at] if is_num(at[0]) or isinstance(at[0], Q) else list(at)
        s = self.sketch(name + "_sketch", on=on)
        s.circles(dq, pts, name=name)
        if depth is None:
            return self.pocket(s, through=True, symmetric=True, name=name + "_cut")
        return self.pocket(s, depth=depth, name=name + "_cut")

    # ---- connectors ------------------------------------------------------------------------
    def connector(self, name, origin, z, x, label):
        """App::LocalCoordinateSystem in the part frame; origin bound to Params (D13 mating frames)."""
        name = self._new_name(name, "connector")
        rot = _rotation(z, x)
        o = self._point(origin, name + "_o")
        lcs = self.container.newObject("App::LocalCoordinateSystem", name)
        lcs.Placement = App.Placement(App.Vector(*[float(c) for c in o]), rot)
        for axis, c in zip("xyz", o):
            if isinstance(c, Q):
                lcs.setExpression(".Placement.Base." + axis, c.expr)
        lcs.addProperty("App::PropertyString", "ConnectorLabel", "AgenticCAD").ConnectorLabel = label
        self.connectors[name] = {"o": o, "z": tuple(rot.multVec(App.Vector(0, 0, 1))),
                                 "x": tuple(rot.multVec(App.Vector(1, 0, 0))), "label": label, "obj": lcs}
        return lcs

    # ---- components ------------------------------------------------------------------------
    def _card(self, component):
        if "/" in component:
            card_id, prefix = component, component.split("/")[-1]
        elif component in context.components:
            card_id, prefix = context.components[component], component
        else:
            raise SDKError("sdk", "unknown component %r; plan components: %s"
                           % (component, sorted(context.components)), action="use a component id from the plan")
        if not card_path(card_id).exists():
            raise SDKError("sdk", "no card %r" % card_id)
        return load_card(card_id), prefix

    def _card_q(self, card, prefix, v):
        """Card value (param name, '-name' or number) as a Q bound to a `<prefix>_<param>` alias."""
        if isinstance(v, str):
            neg, pname = v.startswith("-"), v.lstrip("-")
            if pname not in card["params_mm"]:
                raise SDKError("sdk", "card %s: unknown param %r" % (card["id"], v))
            alias = "%s_%s" % (prefix, pname)
            if alias not in self.params_info:
                self._add_param(alias, card["params_mm"][pname], source="card:" + card["id"])
            q = Q(self.params_info[alias]["value"], "Params." + alias)
            return -q if neg else q
        return 0 if v == 0 else self._q(v, "%s_lit" % prefix, source="card:" + card["id"])

    def _local_datum(self, name, conn, z_local):
        """Frame datum = connector frame translated by z_local along its own z (z_local: 0 or Q)."""
        o, z = conn["o"], conn["z"]
        if not (is_num(z_local) and z_local == 0):
            o = tuple(_add_scaled(c, zc, z_local) for c, zc in zip(o, z))
        rot = _rotation(conn["z"], conn["x"])
        d = Datum(name, self._frame_datum(self._new_name(name, "datum"), o, rot))
        self.datums[name] = d
        return d

    def mount(self, component, at, name=None):
        """Seat a component (plan component id, or a card id) with its card frame on connector `at`:
        cuts the card's mount_pocket (+ clearance), mount_holes (tap fit) and keep_out boxes.
        Card dims become `<component>_<param>` aliases; nothing is a literal in the part program."""
        if at not in self.connectors:
            raise SDKError("sdk", "mount: no connector %r in part %s; declare it with part.connector() first"
                           % (at, self.name))
        card, prefix = self._card(component)
        name = self._new_name(name or "mount_" + prefix, "mount")
        conn = self.connectors[at]
        itf = card.get("interfaces", {})
        clr = self._profile_q("fit_clearance", self.profile["fits"]["clearance"])
        cq = lambda v: self._card_q(card, prefix, v)
        frame = self._local_datum(name + "_frame", conn, 0)
        made = []

        pk = itf.get("mount_pocket")
        if pk:
            if pk.get("top_z", 0) != 0:
                raise SDKError("sdk", "mount_pocket top_z != 0 not supported in SDK v0", location=card["id"])
            l, w, depth = (cq(v) for v in pk["size"])
            s = self.sketch(name + "_pocket_sketch", on=frame)
            s.rect(l + clr, w + clr, center=tuple(cq(v) for v in pk.get("centre_xy", (0, 0))), name="pocket")
            made.append(self.pocket(s, depth=depth + clr / 2, name=name + "_pocket"))

        mh = itf.get("mount_holes")
        if mh:
            if mh.get("pattern") != "rect" or mh.get("z", 0) != 0:
                raise SDKError("sdk", "mount_holes: SDK v0 supports pattern rect at z 0", location=card["id"])
            px, py = (cq(v) / 2 for v in mh["pitch"])
            screw = mh["screw"].lower().replace(".", "_")
            nominal = float(mh["screw"].upper().lstrip("M"))
            sdepth = self._q(2.5 * nominal, "%s_screw_depth" % prefix, source="sdk: 2.5 x nominal")
            s = self.sketch(name + "_holes_sketch", on=frame)
            s.circles(self.hole_d(fit="tap_" + screw), [(px, py), (-px, py), (-px, -py), (px, -py)], name="screw")
            made.append(self.pocket(s, depth=sdepth, name=name + "_holes"))

        for k, ko in enumerate(itf.get("keep_out", [])):
            if "box_min" in ko:
                lo, hi = [cq(v) for v in ko["box_min"]], [cq(v) for v in ko["box_max"]]
                size = [b - a for a, b in zip(lo, hi)]
                centre = [_half_sum(a, b) for a, b in zip(lo[:2], hi[:2])]
                top = hi[2]
            else:
                size = [cq(v) for v in ko["size"]]
                centre = [cq(v) for v in ko.get("centre_xy", (0, 0))]
                top = _plus(cq(ko.get("bottom_z", 0)), size[2])
            d = self._local_datum("%s_keep_out%d_frame" % (name, k), conn, top)
            s = self.sketch("%s_keep_out%d_sketch" % (name, k), on=d)
            s.rect(size[0], size[1], center=tuple(centre), name="box")
            made.append(self.pocket(s, depth=size[2], name="%s_keep_out%d" % (name, k)))
        return made

    def cable_channel(self, start, end, d, name=None):
        """Straight round channel of diameter d from `start` to `end` (part frame). The direction is fixed
        at build time; the start point, length and diameter stay bound to Params. Min wall: gate L7."""
        name = self._new_name(name, "channel")
        s3, e3 = self._point(start, name + "_start"), self._point(end, name + "_end")
        diff = [_minus(b, a) for a, b in zip(s3, e3)]
        vec = App.Vector(*[float(c) for c in diff])
        if vec.Length < 1e-6:
            raise SDKError("sdk", "cable_channel %s: start and end coincide" % name, location=name)
        zdir = -vec.normalize()  # pocket cuts against the datum normal, i.e. toward `end`
        xdir = zdir.cross(App.Vector(0, 0, 1)) if abs(zdir.z) < 0.9 else zdir.cross(App.Vector(1, 0, 0))
        d_frame = Datum(name + "_frame", self._frame_datum(self._new_name(name + "_frame", "datum"), s3,
                                                            _rotation(zdir, xdir.normalize())))
        self.datums[d_frame.name] = d_frame
        terms = ["(%s) ^ 2" % c.expr for c in diff if isinstance(c, Q)]
        length = Q(vec.Length, "sqrt(%s)" % " + ".join(terms)) if terms else self._q(vec.Length, name + "_length")
        s = self.sketch(name + "_sketch", on=d_frame)
        s.circle(d, name="bore")
        return self.pocket(s, depth=length, name=name + "_cut")

    # ---- results ---------------------------------------------------------------------------
    def report(self) -> dict:
        """Recompute and collect the L1/L2/L3 facts (P1 §4.4) for this part."""
        self.doc.recompute()
        sketches = {}
        for name, s in self.sketches.items():
            sk = s.obj
            rc = sk.solve()
            bound = {p.split(".")[-1] for p, _ in sk.ExpressionEngine if p.startswith(".Constraints.")}
            unbound = [c.Name or "#%d" % i for i, c in enumerate(sk.Constraints)
                       if c.Type in DIMENSIONAL and c.Name not in bound]
            sketches[name] = {"solve": rc, "dof": sk.DoF, "fully": bool(sk.FullyConstrained),
                              "conflicting": list(sk.ConflictingConstraints), "redundant": list(sk.RedundantConstraints),
                              "malformed": list(sk.MalformedConstraints), "unbound": unbound}
        invalid = {o.Name: o.getStatusString() for o in self.doc.Objects if "Invalid" in o.State or "Error" in o.State}
        shape = self.body.Shape
        null = shape.isNull()
        bb = None if null else shape.BoundBox
        conns = {}
        for name, c in self.connectors.items():
            plc = c["obj"].Placement
            conns[name] = {"o": list(plc.Base), "z": list(plc.Rotation.multVec(App.Vector(0, 0, 1))),
                           "x": list(plc.Rotation.multVec(App.Vector(1, 0, 0))), "label": c["label"]}
        n_solids = 0 if null else len(shape.Solids)
        volume = 0.0 if null else shape.Volume
        gates = {
            "L1_no_invalid": not invalid,
            "L2_sketches": all(s["solve"] == 0 and s["fully"] and not s["unbound"] for s in sketches.values()),
            "L3_one_solid": n_solids == 1 and volume > 0 and (not null) and shape.isValid(),
        }
        signals = list(self.signals)
        for name, s in sketches.items():
            if not (s["solve"] == 0 and s["fully"]):
                signals.append(signal("failure", "L2", "sketch %s: solve=%d DoF=%d" % (name, s["solve"], s["dof"]), name))
            if s["unbound"]:
                signals.append(signal("failure", "L2", "sketch %s: dimensions without a Params expression: %s"
                                      % (name, s["unbound"]), name))
        for name, msg in invalid.items():
            signals.append(signal("failure", "L1", "%s invalid: %s" % (name, msg), name))
        if not gates["L3_one_solid"]:
            signals.append(signal("failure", "L3", "body has %d solids, volume %.6g mm3" % (n_solids, volume),
                                  "Body", "a pocket may remove everything or a profile may self-intersect"))
        return {
            "part": self.name, "material": self.material, "gates": gates, "signals": signals,
            "sketches": sketches, "invalid": invalid, "n_solids": n_solids, "volume_mm3": volume,
            "bound_box": None if null else [bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax],
            "connectors": conns,
            "params": {a: dict(i, current=_cell_value(self.sheet, a)) for a, i in self.params_info.items()},
        }

    def set_param(self, alias, value, unit="mm"):
        """Change a Params cell (tests, flex checks); call report() or doc.recompute() afterwards."""
        if alias not in self.params_info:
            raise SDKError("sdk", "no param %r" % alias)
        u = "" if unit in ("", "-") else " " + unit
        self.sheet.set(self.sheet.getCellFromAlias(alias), "=%s%s" % (fmt(value), u))

    def save(self, path):
        self.doc.recompute()
        self.doc.saveAs(str(path))
        return str(path)

    def close(self):
        App.closeDocument(self.doc.Name)


def _cell_value(sheet, alias):
    v = sheet.get(alias)
    return v.Value if hasattr(v, "Value") else v


def _plus(a, b):
    return b if (is_num(a) and a == 0) else a + b


def _minus(b, a):
    """b - a where either may be a literal 0."""
    if is_num(a) and a == 0:
        return b
    if is_num(b) and b == 0:
        return -a
    return b - a


def _half_sum(a, b):
    if is_num(a) and is_num(b):
        return (a + b) / 2
    return _plus(a, b) / 2 if not (is_num(b) and b == 0) else a / 2


def _add_scaled(c, k, q):
    """c + k * q for a connector origin component c (0 or Q), constant k, Q q; drops zero terms."""
    if abs(k) < 1e-12:
        return c
    term = q * k if abs(k - 1) > 1e-12 else q
    return _plus(c, term)
