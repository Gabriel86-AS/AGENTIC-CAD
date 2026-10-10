"""Part: one printed part = one FreeCAD document (plan §3.3, P1 §4.2).

    Params   Spreadsheet::Sheet  columns name | value | min | max | source; values "=<v> <unit>" (D18)
    <name>   App::Part           holds Body + one App::LocalCoordinateSystem per connector
    Body     PartDesign::Body    datums, sketches, pads, pockets; never attached to faces (D19)

Every length the program passes (number, plan param or Q expression) ends up as an expression of a
Params alias; literals are promoted to aliases named after the call (`source = literal`).
Frames (`frame=` datums, connectors, mount frames) are relative to the Body origin = part frame.
A frame is one whole-Placement expression (D28, probed 2026-10-09):
    placement(vector(x; y; z); rotationz(yaw) * rotationy(pitch) * rotationx(roll))
so origin and orientation both follow Params; per-component Yaw/Pitch/Roll bindings fight at gimbal lock.
Origin planes (probed 2026-10-09): XY sketch (x, y) -> (X, Y), normal +Z; XZ -> (X, Z), normal -Y;
YZ -> (Y, Z), normal +X. Pad grows along the sketch normal, Pocket cuts against it.
"""
import math

import FreeCAD as App

from components.card import card_path, evaluate, is_expr, load_card
from profiles import load_profile
from schemas.names import RESERVED_PART_NAMES, name_error
from sdk.errors import SDKError, signal
from sdk.expr import Q, fmt, is_num, qmax
from sdk.sketch import Sketch

PLANES = ("XY", "XZ", "YZ")
DIMENSIONAL = {"Distance", "DistanceX", "DistanceY", "Radius", "Diameter", "Angle"}
RESERVED = RESERVED_PART_NAMES


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


def _is_zero(v):
    return is_num(v) and v == 0


def _frame_expr(o, angles):
    """Placement expression for origin o (number|Q x3, mm) and yaw/pitch/roll angles (number|Q x3, deg).
    Numbers stay literal: callers pass numbers only for structural values (0, a wall's face normal)."""
    term = lambda v, u: v.expr if isinstance(v, Q) else "%s %s" % (fmt(float(v)), u)
    vec = "; ".join(term(c, "mm") for c in o)
    rot = " * ".join("rotation%s(%s)" % (ax, term(a, "deg")) for ax, a in zip("zyx", angles) if not _is_zero(a))
    return "placement(vector(%s); %s)" % (vec, rot or "rotationz(0 deg)")


def _frame_placement(o, angles):
    return App.Placement(App.Vector(*[float(c) for c in o]), App.Rotation(*[float(a) for a in angles]))


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
            if self.params_info[alias]["source"] == "plan":
                raise SDKError("sdk", "param %r is a plan param; read it as P.%s instead of redefining it" % (alias, alias),
                               action="use P.%s directly, or give the part-local value another name" % alias)
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
                if alias in v.plan and v.expr == "Params." + alias:  # name=P.name: the plan param itself
                    out[alias] = v
                    continue
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
    def _frame_datum(self, name, frame):
        """PartDesign::Plane on the XY origin plane; AttachmentOffset = frame (origin + orientation bound)."""
        o, angles = frame
        pl = self.body.newObject("PartDesign::Plane", name)
        pl.AttachmentSupport = [(self._origin("XY_Plane"), "")]
        pl.MapMode = "FlatFace"
        pl.AttachmentOffset = _frame_placement(o, angles)
        pl.setExpression(".AttachmentOffset", _frame_expr(o, angles))
        return pl

    def _point(self, p, hint):
        return tuple(0 if _is_zero(c) else self._q(c, "%s_%s" % (hint, a)) for a, c in zip("xyz", p))

    def _angles(self, hint, z=None, x=None, rot=None):
        """Frame orientation as yaw/pitch/roll (0|Q deg), from `rot` = (yaw, pitch, roll) in degrees
        (numbers or deg params) or from the `z`/`x` axes; non-zero literals become `<hint>_yaw` ... aliases."""
        if rot is not None:
            if z is not None or x is not None:
                raise SDKError("sdk", "%s: pass either rot=(yaw, pitch, roll) or z=/x=, not both" % hint)
            if len(rot) != 3:
                raise SDKError("sdk", "%s: rot must be (yaw, pitch, roll) in degrees" % hint)
            ypr = rot
        else:
            ypr = [round(a, 9) for a in _rotation(z or (0, 0, 1), x or (1, 0, 0)).toEulerAngles("YawPitchRoll")]
        out = []
        for k, a in zip(("yaw", "pitch", "roll"), ypr):
            if isinstance(a, Q) and a.unit != "deg":
                raise SDKError("sdk", "%s: %s must be in deg, got %r" % (hint, k, a),
                               action="define the param with unit 'deg'")
            out.append(0 if _is_zero(a) else self._q(a, "%s_%s" % (hint, k), unit="deg"))
        return tuple(out)

    def _frame(self, hint, origin, z=None, x=None, rot=None):
        """(origin, angles) with every non-zero component a Q bound to Params."""
        return self._point(origin, hint + "_o"), self._angles(hint, z, x, rot)

    def datum(self, name=None, plane="XY", offset=0, origin=None, z=None, x=None, rot=None):
        """Datum plane: `plane` + `offset` along its normal, or a full frame (`origin` + `z`/`x` axes or
        `rot` = (yaw, pitch, roll) deg). A sketch on it has its x along the datum x, its normal along z."""
        name = self._new_name(name, "datum")
        if origin is not None or z is not None or x is not None or rot is not None:
            pl = self._frame_datum(name, self._frame(name, origin or (0, 0, 0), z, x, rot))
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
        f.SideType = "Symmetric" if symmetric else "One side"
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
        f.SideType = "Symmetric" if symmetric else "One side"
        return f

    def hole_d(self, d=None, fit="clearance", horizontal=False):
        """Printed hole diameter as a Q: `fit` from the profile `fits` (d + allowance), a screw fit
        like "clearance_m3" / "tap_m3" (profile `screws`, d ignored) or a heat-set insert fit like
        "insert_m3" (card fastener/m3_insert, d ignored). horizontal adds the profile's extra allowance."""
        kind, _, thread = fit.partition("_")
        if kind == "insert":
            card, prefix = self._fastener(thread, "insert", fit)
            q = self._card_q(card, prefix, card["interfaces"]["hole"]["d"])
        elif thread:
            screws = {k.lower().replace(".", "_"): v for k, v in self.profile["screws"].items()}
            if thread not in screws or kind not in screws[thread]:
                raise SDKError("sdk", "unknown screw fit %r; screws: %s, kinds: clearance, tap"
                               % (fit, sorted(screws)), action="use e.g. fit='clearance_m3'")
            q = self._profile_q("%s_%s" % (kind, thread), screws[thread][kind])
        else:
            if fit not in self.profile["fits"]:
                raise SDKError("sdk", "unknown fit %r; profile fits: %s, a screw fit like tap_m3 or insert_m3"
                               % (fit, sorted(self.profile["fits"])))
            if d is None:
                raise SDKError("sdk", "fit %r needs a nominal diameter d" % fit)
            q = self._q(d, "hole_nominal_d") + self._profile_q("fit_" + fit, self.profile["fits"][fit])
        if horizontal:
            q = q + self._profile_q("horizontal_hole_extra", self.profile["horizontal_hole_extra_mm"])
        return q

    def _fastener(self, thread, kind, fit):
        """Card fastener/<thread>_<kind> (e.g. m3_insert) and its alias prefix."""
        card_id = "fastener/%s_%s" % (thread, kind)
        if not card_path(card_id).exists():
            raise SDKError("sdk", "%r needs card %s, which is not in the library" % (fit, card_id),
                           action="use a screw size with a %s card (e.g. m3)" % kind)
        return load_card(card_id), "%s_%s" % (thread, kind)

    def hole(self, on, at, d=None, fit="clearance", depth=None, horizontal=False, counterbore=False, name=None):
        """Round hole(s) at `at` = (u, v) or a list of points in the sketch frame of `on`; diameter from
        the print profile (see hole_d). Through all along the normal unless `depth` is given; an insert
        fit defaults to the card's hole depth. counterbore=True (screw clearance fits only) also sinks
        the screw head: head_d + clearance, head_h deep, cut against the normal of `on`."""
        name = self._new_name(name, "hole")
        dq = self.hole_d(d, fit, horizontal)
        kind, _, thread = fit.partition("_")
        if kind == "insert" and depth is None:
            card, prefix = self._fastener(thread, "insert", fit)
            depth = self._card_q(card, prefix, card["interfaces"]["hole"]["depth"])
        pts = [at] if is_num(at[0]) or isinstance(at[0], Q) else list(at)
        s = self.sketch(name + "_sketch", on=on)
        s.circles(dq, pts, name=name)
        cut = (self.pocket(s, through=True, symmetric=True, name=name + "_cut") if depth is None
               else self.pocket(s, depth=depth, name=name + "_cut"))
        if not counterbore:
            return cut
        if kind != "clearance" or not thread:
            raise SDKError("sdk", "hole %s: counterbore needs a screw clearance fit such as clearance_m3" % name,
                           location=name)
        card, prefix = self._fastener(thread, "screw", fit)
        cb = card["interfaces"]["counterbore"]
        clr = self._profile_q("fit_clearance", self.profile["fits"]["clearance"])
        s2 = self.sketch(name + "_counterbore_sketch", on=on)
        s2.circles(self._card_q(card, prefix, cb["d"]) + clr, pts, name=name + "_head")
        return [cut, self.pocket(s2, depth=self._card_q(card, prefix, cb["depth"]) + clr / 2,
                                 name=name + "_counterbore")]

    # ---- connectors ------------------------------------------------------------------------
    def connector(self, name, origin, z=None, x=None, label=None, rot=None):
        """App::LocalCoordinateSystem in the part frame (D13 mating frames); orientation from the `z`/`x`
        axes or `rot` = (yaw, pitch, roll) deg. Origin and orientation are bound to Params."""
        if label is None:
            raise SDKError("sdk", "connector %s: label is required" % name)
        if rot is None and (z is None or x is None):
            raise SDKError("sdk", "connector %s: pass z= and x= axes, or rot=(yaw, pitch, roll)" % name)
        name = self._new_name(name, "connector")
        frame = self._frame(name, origin, z, x, rot)
        lcs = self.container.newObject("App::LocalCoordinateSystem", name)
        lcs.Placement = _frame_placement(*frame)
        lcs.setExpression(".Placement", _frame_expr(*frame))
        lcs.addProperty("App::PropertyString", "ConnectorLabel", "AgenticCAD").ConnectorLabel = label
        self.connectors[name] = {"frame": frame, "label": label, "obj": lcs}
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

    def _card_alias(self, card, prefix, pname):
        alias = "%s_%s" % (prefix, pname)
        if alias not in self.params_info:
            self._add_param(alias, card["params_mm"][pname], source="card:" + card["id"])
        return Q(self.params_info[alias]["value"], "Params." + alias)

    def _card_q(self, card, prefix, v):
        """Card value (param name, param expression or number) as a Q over `<prefix>_<param>` aliases."""
        if isinstance(v, str):
            if not is_expr(card, v):
                raise SDKError("sdk", "card %s: %r is not a param or an expression over params_mm" % (card["id"], v))
            return evaluate(card, v, lambda pname: self._card_alias(card, prefix, pname))
        return 0 if v == 0 else self._q(v, "%s_lit" % prefix, source="card:" + card["id"])

    def _local_datum(self, name, base, origin=(0, 0, 0), angles=(0, 0, 0)):
        """Datum plane attached to datum `base`, offset by a frame in base's own axes (probed 2026-10-09:
        the attachment composes as base.Placement * offset). origin/angles: 0, numbers or Qs."""
        name = self._new_name(name, "datum")
        pl = self.body.newObject("PartDesign::Plane", name)
        pl.AttachmentSupport = [(base.obj, "")]
        pl.MapMode = "FlatFace"
        if not all(_is_zero(v) for v in (*origin, *angles)):
            pl.AttachmentOffset = _frame_placement(origin, angles)
            pl.setExpression(".AttachmentOffset", _frame_expr(origin, angles))
        d = Datum(name, pl)
        self.datums[name] = d
        return d

    def _connector_frame(self, at, call, name):
        """Datum at connector `at` (the frame a card is seated on)."""
        if at not in self.connectors:
            raise SDKError("sdk", "%s: no connector %r in part %s; declare it with part.connector() first"
                           % (call, at, self.name))
        fname = self._new_name(name + "_frame", "datum")
        d = Datum(fname, self._frame_datum(fname, self.connectors[at]["frame"]))
        self.datums[fname] = d
        return d

    def mount(self, component, at, name=None, host_t=None):
        """Seat a component (plan component id, or a card id) with its card frame on connector `at`:
        body pocket (+ clearance), tapped mounting holes, standoffs and keep-out cuts, all from the card.
        Card dims become `<component>_<param>` aliases; nothing is a literal in the part program.
        host_t: material thickness under the frame; needed by slotted standoffs (nut traps from below)."""
        card, prefix = self._card(component)
        name = self._new_name(name or "mount_" + prefix, "mount")
        frame = self._connector_frame(at, "mount", name)
        return self._seat(card, prefix, frame, name, host_t)

    def _seat(self, card, prefix, frame, name, host_t=None):
        itf = card.get("interfaces", {})
        clr = self._profile_q("fit_clearance", self.profile["fits"]["clearance"])
        cq = lambda v: self._card_q(card, prefix, v)
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
            screw = mh["screw"].lower().replace(".", "_")
            s = self.sketch(name + "_holes_sketch", on=frame)
            s.circles(self.hole_d(fit="tap_" + screw), _rect_pts(*(cq(v) / 2 for v in mh["pitch"])), name="screw")
            made.append(self.pocket(s, depth=self._screw_depth(mh["screw"], prefix), name=name + "_holes"))

        so = itf.get("standoffs")
        if so:
            made += self._standoffs(card, prefix, frame, name, so, host_t)

        for k, ko in enumerate(itf.get("keep_out", [])):
            if "box_min" in ko:
                lo, hi = [cq(v) for v in ko["box_min"]], [cq(v) for v in ko["box_max"]]
                size = [_minus(b, a) for a, b in zip(lo, hi)]
                centre = [_half_sum(a, b) for a, b in zip(lo[:2], hi[:2])]
                top = hi[2]
            else:
                size = [cq(v) for v in ko["size"]]
                centre = [cq(v) for v in ko.get("centre_xy", (0, 0))]
                top = _plus(cq(ko.get("bottom_z", 0)), size[2])
            d = self._local_datum("%s_keep_out%d_frame" % (name, k), frame, (0, 0, top))
            s = self.sketch("%s_keep_out%d_sketch" % (name, k), on=d)
            s.rect(size[0], size[1], center=tuple(centre), name="box")
            made.append(self.pocket(s, depth=size[2], name="%s_keep_out%d" % (name, k)))
        return made

    def _screw_depth(self, screw, prefix):
        nominal = float(screw.upper().lstrip("M"))
        return self._q(2.5 * nominal, "%s_screw_depth" % prefix, source="sdk: 2.5 x nominal")

    def _standoffs(self, card, prefix, frame, name, so, host_t):
        """Bosses from z = 0 to h. rect: tapped holes h deep. rect_slotted: obround bosses along x, a
        clearance slot through boss and host (host_t), and a nut-trap slot from the host underside."""
        cq = lambda v: self._card_q(card, prefix, v)
        h, d = cq(so["h"]), cq(so["d"])
        px, py = (cq(v) / 2 for v in so["pitch"])
        screw = so["screw"].lower().replace(".", "_")
        if so["pattern"] not in ("rect", "rect_slotted"):
            raise SDKError("sdk", "standoffs: SDK v0 supports patterns rect and rect_slotted, got %r"
                           % so["pattern"], location=card["id"])
        if so["pattern"] == "rect_slotted" and host_t is None:
            raise SDKError("sdk", "%s: slotted standoffs need the host thickness for their nut traps" % name,
                           location=card["id"], action="use part.electronics_bay(), or pass host_t= to mount()")
        top = self._local_datum(name + "_standoff_top", frame, (0, 0, h))
        boss = self.sketch(name + "_standoff_sketch", on=frame)
        made = []
        if so["pattern"] == "rect":
            boss.circles(d, _rect_pts(px, py), name="standoff")
            made.append(self.pad(boss, h, name=name + "_standoffs"))
            s = self.sketch(name + "_standoff_holes_sketch", on=top)
            s.circles(self.hole_d(fit="tap_" + screw), _rect_pts(px, py), name="screw")
            made.append(self.pocket(s, depth=h, name=name + "_standoff_holes"))
            return made
        host_t = self._q(host_t, name + "_host_t")
        half = cq(so["slot_len"]) / 2
        ends = [((cx - half, cy), (cx + half, cy)) for cx, cy in _rect_pts(px, py)]
        for k, (a, b) in enumerate(ends):
            boss.slot(a, b, d, name="standoff%d" % k)
        made.append(self.pad(boss, h, name=name + "_standoffs"))
        s = self.sketch(name + "_slots_sketch", on=top)
        for k, (a, b) in enumerate(ends):
            s.slot(a, b, cq(so["slot_w"]), name="slot%d" % k)
        made.append(self.pocket(s, depth=h + host_t, name=name + "_slots"))
        nut_card, nut_prefix = self._fastener(screw, "screw", so["screw"])
        trap = nut_card["interfaces"]["nut_trap"]
        clr = self._profile_q("fit_clearance", self.profile["fits"]["clearance"])
        nut_w = self._card_q(nut_card, nut_prefix, trap["s"]) + clr
        nut_depth = self._card_q(nut_card, nut_prefix, trap["m"]) + clr / 2
        under = self._local_datum(name + "_nut_trap_top", frame, (0, 0, nut_depth - host_t))
        s = self.sketch(name + "_nut_traps_sketch", on=under)
        for k, (a, b) in enumerate(ends):
            s.slot(a, b, nut_w, name="nut%d" % k)
        made.append(self.pocket(s, depth=nut_depth, name=name + "_nut_traps"))
        return made

    def mate_to(self, target, at, access=True, name=None):
        """Cut a component's drive interface where this part is driven by it: `target` =
        "<component>.<card connector>" (e.g. "s_shoulder.horn"), seated on this part's connector `at`
        (frames coincide; z points from the component into this part). For a disc horn: a recess for
        the disc (+ clearance), tapped holes for the horn screws and, with access, a through hole for
        the screwdriver on the axis. Give the joint the same axis (connector z) as `at`."""
        comp, _, cname = target.partition(".")
        if not cname:
            raise SDKError("sdk", "mate_to: target must be '<component>.<connector>', got %r" % target)
        card, prefix = self._card(comp)
        horns = {k: v for k, v in card.get("interfaces", {}).items()
                 if isinstance(v, dict) and v.get("kind") == "disc_horn"}
        hz = next((v for v in horns.values() if v.get("at") == cname), None)
        if hz is None:
            at_list = sorted(v["at"] for v in horns.values())
            raise SDKError("sdk", "mate_to: card %s has no drive interface at connector %r; disc horns at: %s"
                           % (card["id"], cname, at_list),
                           action=("target e.g. '%s.%s'" % (comp, at_list[0])) if at_list else None)
        name = self._new_name(name or "mate_" + prefix, "mate")
        frame = self._connector_frame(at, "mate_to", name)
        clr = self._profile_q("fit_clearance", self.profile["fits"]["clearance"])
        cq = lambda v: self._card_q(card, prefix, v)
        disc_t = cq(hz["disc_t"])
        ceiling = self._local_datum(name + "_recess_top", frame, (0, 0, disc_t + clr / 2))
        s = self.sketch(name + "_recess_sketch", on=ceiling)
        s.circle(cq(hz["disc_d"]) + clr, name="disc")
        made = [self.pocket(s, depth=disc_t + clr, name=name + "_recess")]
        r, n = cq(hz["bolt_circle_d"]) / 2, int(hz["bolts"])
        pts = []
        for k in range(n):
            c, sn = math.cos(2 * math.pi * k / n), math.sin(2 * math.pi * k / n)
            pts.append(tuple(0 if abs(v) < 1e-12 else r * v for v in (c, sn)))
        screw = hz["screw"].lower().replace(".", "_")
        s = self.sketch(name + "_screws_sketch", on=ceiling)
        s.circles(self.hole_d(fit="tap_" + screw), pts, name="screw")
        made.append(self.pocket(s, depth=self._screw_depth(hz["screw"], prefix + "_horn"), reverse=True,
                                name=name + "_screws"))
        if access:
            s = self.sketch(name + "_access_sketch", on=ceiling)
            s.circle(cq(hz["access_d"]), name="access")
            made.append(self.pocket(s, through=True, reverse=True, name=name + "_access"))
        return made

    def electronics_bay(self, component, at, wall=None, margin=2, floor=None, name=None):
        """Open-top bay for an electronics card (generic bay or a named board), seated on connector `at`
        (the card frame: z up out of the floor). Builds a floor under z = 0 (default thick enough for the
        card's cuts below it + min wall), walls around the card `volume` (+ margin) up to its top, then
        the card's standoffs / pockets / keep-outs (as mount) and wall openings. Params: <name>_wall,
        _margin, _floor_t; floor and walls follow the card dims."""
        card, prefix = self._card(component)
        if not str(card.get("kind", "")).startswith("electronics"):
            raise SDKError("sdk", "electronics_bay: %s is a %s, not an electronics card" % (card["id"], card.get("kind")))
        vol = card.get("interfaces", {}).get("volume")
        if not vol:
            raise SDKError("sdk", "electronics_bay: card %s has no `volume` interface" % card["id"])
        name = self._new_name(name or "bay_" + prefix, "bay")
        frame = self._connector_frame(at, "electronics_bay", name)
        cq = lambda v: self._card_q(card, prefix, v)
        if wall is None:
            wall = self._q(2 * self.profile["min_wall_mm"], name + "_wall", source="sdk: 2 x min_wall")
        else:
            wall = self._q(wall, name + "_wall")
        margin = self._q(margin, name + "_margin")
        vl, vw, vh = (cq(v) for v in vol["size"])
        cx, cy = (cq(v) for v in vol.get("centre_xy", (0, 0)))
        height = _plus(cq(vol.get("bottom_z", 0)), vh)
        in_l, in_w = vl + margin * 2, vw + margin * 2
        out_l, out_w = in_l + wall * 2, in_w + wall * 2
        if floor is None:
            min_wall = self._profile_q("min_wall", self.profile["min_wall_mm"])
            floor_t = self._add_param(self._unique_alias(name + "_floor_t"),
                                      qmax(self._q(3, name + "_floor_min", source="sdk"),
                                           *[b + min_wall for b in self._below(card, prefix)]),
                                      source="sdk: card cuts below the frame + min_wall")
        else:
            floor_t = self._q(floor, name + "_floor_t")
        made = []
        s = self.sketch(name + "_floor_sketch", on=frame).rect(out_l, out_w, center=(cx, cy), name="floor")
        made.append(self.pad(s, floor_t, reverse=True, name=name + "_floor"))
        s = self.sketch(name + "_walls_sketch", on=frame)
        s.rect(out_l, out_w, center=(cx, cy), name="outer").rect(in_l, in_w, center=(cx, cy), name="inner")
        made.append(self.pad(s, height, name=name + "_walls"))
        made += self._seat(card, prefix, frame, name, host_t=floor_t)
        faces = {"+x": ((1, 0, 0), (0, 1, 0)), "-x": ((-1, 0, 0), (0, -1, 0)),
                 "+y": ((0, 1, 0), (-1, 0, 0)), "-y": ((0, -1, 0), (1, 0, 0))}
        clr = self._profile_q("fit_clearance", self.profile["fits"]["clearance"])
        for k, op in enumerate(card["interfaces"].get("openings", [])):
            if op["face"] not in faces:
                continue  # "+z" (lid vents): not cut in v0
            z, x = faces[op["face"]]
            half = (in_l if z[0] else in_w) / 2 + wall
            origin = (_plus(cx, half * z[0]) if z[0] else cx, _plus(cy, half * z[1]) if z[1] else cy, 0)
            angles = [round(a, 9) for a in _rotation(z, x).toEulerAngles("YawPitchRoll")]
            d = self._local_datum("%s_opening%d_frame" % (name, k), frame, origin, angles)
            w, h = (cq(v) for v in op["size"])
            s = self.sketch("%s_opening%d_sketch" % (name, k), on=d)
            s.rect(w, h, center=(0, _plus(cq(op.get("bottom_z", 0)), h / 2)), name=op.get("kind", "opening"))
            made.append(self.pocket(s, depth=wall + clr, name="%s_opening%d" % (name, k)))
        return made

    def _below(self, card, prefix):
        """Depths (Q, > 0) that the card's cuts reach below its frame (z < 0)."""
        itf = card.get("interfaces", {})
        cq = lambda v: self._card_q(card, prefix, v)
        clr = self._profile_q("fit_clearance", self.profile["fits"]["clearance"])
        out = []
        if itf.get("mount_pocket"):
            out.append(cq(itf["mount_pocket"]["size"][2]) + clr / 2)
        for ko in itf.get("keep_out", []):
            bottom = cq(ko["box_min"][2]) if "box_min" in ko else cq(ko.get("bottom_z", 0))
            if not _is_zero(bottom) and bottom.value < 0:
                out.append(-bottom)
        so = itf.get("standoffs")
        if so and so["pattern"] == "rect_slotted":
            nut_card, nut_prefix = self._fastener(so["screw"].lower().replace(".", "_"), "screw", so["screw"])
            out.append(self._card_q(nut_card, nut_prefix, nut_card["interfaces"]["nut_trap"]["m"]) + clr / 2)
        return out

    def cable_channel(self, start, end, d, name=None):
        """Straight round channel of diameter d from `start` to `end` (part frame). Start, end and d are
        bound to Params: editing either end moves, turns and resizes the channel. Min wall: gate L7."""
        name = self._new_name(name, "channel")
        s3, e3 = self._point(start, name + "_start"), self._point(end, name + "_end")
        diff = [_minus(b, a) for a, b in zip(s3, e3)]
        dx, dy, dz = (float(c) for c in diff)
        length_v = math.sqrt(dx * dx + dy * dy + dz * dz)
        if length_v < 1e-6:
            raise SDKError("sdk", "cable_channel %s: start and end coincide" % name, location=name)
        # datum z = -(end - start)/L, so the pocket (cutting against z) runs toward `end`:
        # z = Rz(yaw)·Ry(pitch)·ẑ = (cos yaw sin pitch, sin yaw sin pitch, cos pitch)
        dxe, dye, dze = ("(%s)" % c.expr if isinstance(c, Q) else "(0 mm)" for c in diff)
        length = Q(length_v, "sqrt(%s ^ 2 + %s ^ 2 + %s ^ 2)" % (dxe, dye, dze))
        yaw = Q(math.degrees(math.atan2(-dy, -dx)), "atan2(-%s; -%s)" % (dye, dxe), "deg")
        pitch = Q(math.degrees(math.acos(max(-1.0, min(1.0, -dz / length_v)))),
                  "acos(-%s / %s)" % (dze, length.expr), "deg")
        frame_name = self._new_name(name + "_frame", "datum")
        d_frame = Datum(frame_name, self._frame_datum(frame_name, (s3, (yaw, pitch, 0))))
        self.datums[frame_name] = d_frame
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

    def set_param(self, alias, value, unit=None):
        """Change a Params cell (tests, flex checks; unit defaults to the param's own);
        call report() or doc.recompute() afterwards."""
        if alias not in self.params_info:
            raise SDKError("sdk", "no param %r" % alias)
        unit = self.params_info[alias]["unit"] if unit is None else unit
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


def _rect_pts(px, py):
    """Four corners (±px, ±py) of a centred rectangular pattern."""
    return [(px, py), (-px, py), (-px, -py), (px, -py)]


def _half_sum(a, b):
    if is_num(a) and is_num(b):
        return (a + b) / 2
    return _plus(a, b) / 2 if not (is_num(b) and b == 0) else a / 2

