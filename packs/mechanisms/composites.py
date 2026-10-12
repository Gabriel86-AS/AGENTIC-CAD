"""Mechanisms pack composites (P2 T2.8), registered on sdk.Part. Run inside freecadcmd.

Two-sided calls (hinge, slide_rail, snap_hook) are made by BOTH parts of a pair on connectors with the same
frame at rest (D13: joint frames coincide at q = 0; a latch pair is placed the same way by the plan), with
side "a" on one part and "b" on the other; the two halves never overlap at rest. spur_gear is one-sided:
each gear of a pair calls it on its own axle connector, the follower with mesh="gap".

    hinge        knuckles along z of `at` (z = hinge axis, z 0..length); a: even knuckles, b: odd ones;
                 pin bore through the own knuckles; optional webs to the leaf and relief cuts
    slide_rail   dovetail (or flat) rail along z of `at` (z = slide direction): a = rail, b = groove + fit
    spur_gear    involute spur gear on z of `at` (PartDesign InvoluteGear, teeth + module bound to Params)
    snap_hook    cantilever snap fit: a = beam along z with a lip toward +x, b = the window the lip enters
"""
import math

import InvoluteGearFeature

from sdk.errors import SDKError
from sdk.expr import Q, is_num
from sdk.part import Part, ParamSet

# pin -> (side a fit, side b fit, default knuckle od): a holds the pin, b turns on it
PINS = {"m3": ("tap_m3", "clearance_m3", 8), "m3_bolt": ("clearance_m3", "clearance_m3", 8),
        "filament": ("press", "clearance", 6)}
FILAMENT_D = 1.75
SNAP_STRAIN = {"PETG": 0.03, "PLA": 0.015}   # allowed bending strain of a printed snap beam (repeated use)


def _side(call, side):
    if side not in ("a", "b"):
        raise SDKError("sdk", "%s: side must be 'a' or 'b', got %r" % (call, side))


def _name(self, name, base, kind):
    """The given name, or `base` made unique in this part (two hinges in one part need no names)."""
    if name is None:
        k, name = 2, base
        while name in self._names:
            name, k = "%s_%d" % (base, k), k + 1
    return self._new_name(name, kind)


def _deg(self, v, hint):
    return 0 if is_num(v) and v == 0 else self._q(v, hint, unit="deg")


def hinge(self, at, side, length, knuckles=3, od=None, pin="m3", gap=0.5, web=0, web_angle=0, relief=False,
          horizontal=False, name=None):
    """Pin hinge on connector `at` (z = hinge axis; both parts declare it with the same frame). The knuckle
    stack runs z = 0..length in `knuckles` equal segments with `gap` between neighbours; side "a" builds the
    even segments (both ends when knuckles is odd), side "b" the odd ones. Each knuckle is a cylinder `od`
    wide, or with `web` > 0 an obround from the axis to `web` mm along `web_angle` (deg, in the frame's xy
    plane) that joins it to the part's leaf. pin: "m3" (screw: a tapped, b clearance), "m3_bolt" (both
    clearance: screw + nut, a friction joint), "filament" (1.75 mm: a press, b clearance); the bore runs
    through the own knuckles. relief=True cuts od + 2 x gap cylinders where the other side's knuckles turn
    (for a leaf that reaches the axis). horizontal: the axis lies horizontal in the print (bore allowance).
    Returns length, od, gap, seg; params <name>_length, _od, _gap, _web, _web_angle."""
    _side("hinge", side)
    if not isinstance(knuckles, int) or knuckles < 2:
        raise SDKError("sdk", "hinge: knuckles must be an int >= 2, got %r" % (knuckles,))
    if pin not in PINS:
        raise SDKError("sdk", "hinge: pin must be one of %s, got %r" % (sorted(PINS), pin))
    name = _name(self, name, "hinge_" + side, "hinge")
    frame = self._connector_frame(at, "hinge", name)
    L, g = self._q(length, name + "_length"), self._q(gap, name + "_gap")
    od = self._q(PINS[pin][2] if od is None else od, name + "_od")
    w = 0 if is_num(web) and web == 0 else self._q(web, name + "_web")
    ang = _deg(self, web_angle, name + "_web_angle")
    seg = L / knuckles
    if (seg - g).value <= 0:
        raise SDKError("sdk", "hinge %s: knuckle segment %g mm <= gap %g mm" % (name, seg.value, g.value),
                       location=name, action="lengthen the hinge or use fewer knuckles")

    def span(i, grow):
        """z range of segment i: shrunk by gap / 2 at inner ends (knuckle) or grown by it (relief)."""
        k = -1 if grow else 1
        z0 = 0 if i == 0 else seg * i + g / 2 * k
        z1 = L if i == knuckles - 1 else seg * (i + 1) - g / 2 * k
        return z0, z1

    made = []
    for i in range(knuckles):
        own = (i % 2 == 0) == (side == "a")
        if not own and not relief:
            continue
        z0, z1 = span(i, grow=not own)
        tag = "%s_k%d" % (name, i) if own else "%s_relief%d" % (name, i)
        if own:
            d = self._local_datum(tag + "_plane", frame, (0, 0, z0), (ang, 0, 0))
            s = self.sketch(tag + "_sketch", on=d)
            s.slot((0, 0), (w, 0), od, name="knuckle") if not is_num(w) else s.circle(od, name="knuckle")
            made.append(self.pad(s, z1 - z0, name=tag))
        else:
            d = self._local_datum(tag + "_plane", frame, (0, 0, z1))
            s = self.sketch(tag + "_sketch", on=d).circle(od + g * 2, name="relief")
            made.append(self.pocket(s, depth=z1 - z0, name=tag))
    fit = PINS[pin][0 if side == "a" else 1]
    top = self._local_datum(name + "_pin_top", frame, (0, 0, L))
    made.append(self.hole(top, at=(0, 0), d=FILAMENT_D if pin == "filament" else None, fit=fit, depth=L,
                          horizontal=horizontal, name=name + "_pin"))
    return ParamSet({"length": L, "od": od, "gap": g, "seg": seg, "made": made})


def slide_rail(self, at, side, length=None, width=8, height=4, flare=2, offset=(0, 0), angle=0, embed=1,
               fit="clearance", name=None):
    """Linear guide along z of connector `at` (z = the slider joint axis; both parts declare it with the same
    frame). The profile sits at `offset` = (x, y) in the frame, turned by `angle` (deg) about z; in that local
    frame the rail stands on its host face at y = 0 and grows toward +y: `width` at the face, `width + flare`
    at y = `height` (a dovetail; flare=0: a flat rib), `embed` mm into the host below y = 0.
      side "a": the rail, padded from z = 0 to `length`.
      side "b": the groove = rail + `fit` allowance (half per side, and on top), cut over z = 0..length, or
                with length=None through the part both ways (an open-ended guide the rail slides into).
    Returns width, height, flare, clearance; params <name>_width, _height, _flare, _length."""
    _side("slide_rail", side)
    if fit not in self.profile["fits"]:
        raise SDKError("sdk", "slide_rail: unknown fit %r; profile fits: %s" % (fit, sorted(self.profile["fits"])))
    if side == "a" and length is None:
        raise SDKError("sdk", "slide_rail: the rail (side 'a') needs a length")
    name = _name(self, name, "rail_" + side, "rail")
    frame = self._connector_frame(at, "slide_rail", name)
    w, h, e = self._q(width, name + "_width"), self._q(height, name + "_height"), self._q(embed, name + "_embed")
    fl = 0 if is_num(flare) and flare == 0 else self._q(flare, name + "_flare")
    c = self._profile_q("fit_" + fit, self.profile["fits"][fit]) if side == "b" else 0
    ox, oy = offset
    plane = self._local_datum(name + "_plane", frame, (ox, oy, 0), (_deg(self, angle, name + "_angle"), 0, 0))
    hw = w / 2 + c / 2 if side == "b" else w / 2
    top = h + c / 2 if side == "b" else h
    if is_num(fl):
        pts = [(-hw, -e), (hw, -e), (hw, top), (-hw, top)]
    else:
        pts = [(-hw, -e), (hw, -e), (hw, 0), (hw + fl / 2, top), (-hw - fl / 2, top), (-hw, 0)]
    s = self.sketch(name + "_sketch", on=plane).polyline(pts, name="profile")
    if side == "a":
        made = self.pad(s, self._q(length, name + "_length"), name=name + "_rail")
    elif length is None:
        made = self.pocket(s, through=True, symmetric=True, name=name + "_groove")
    else:
        made = self.pocket(s, depth=self._q(length, name + "_length"), reverse=True, name=name + "_groove")
    return ParamSet({"width": w, "height": h, "flare": fl, "clearance": c, "made": made})


def spur_gear(self, at, module, teeth, thick, bore="clearance_m3", bore_d=None, mesh="tooth", mesh_angle=0,
              pressure_angle=20, name=None):
    """Involute spur gear on connector `at` (z = gear axis = the joint axis; the gear spans z = 0..thick).
    Teeth and module are bound to Params (FreeCAD PartDesign InvoluteGear), so a module edit re-cuts the
    teeth; `teeth` is a whole number >= 8 (a plan param of unit "-" or an int). At rest a tooth (mesh="tooth")
    or a tooth gap (mesh="gap") is centred on `mesh_angle` (deg from x in the frame's xy plane): point it at
    the mating gear; the driver takes "tooth", the follower "gap". Centre distance of a pair =
    module x (teeth_1 + teeth_2) / 2 (+ up to 0.2 mm for print backlash); couple the joints with mimic
    multiplier -teeth_1 / teeth_2 (axes parallel, same direction). `bore` = a hole fit on the axis
    ("clearance_m3" turns on an M3 screw axle; "press" / "clearance" + bore_d; None: solid).
    Returns module, teeth, thick, pitch_d, tip_d, root_d; params <name>_module, _teeth, _thick, _mesh_angle."""
    if mesh not in ("tooth", "gap"):
        raise SDKError("sdk", "spur_gear: mesh must be 'tooth' or 'gap', got %r" % (mesh,))
    if is_num(teeth) and (int(teeth) != teeth or teeth < 8):
        raise SDKError("sdk", "spur_gear: teeth must be a whole number >= 8, got %r" % (teeth,))
    name = _name(self, name, "gear", "gear")
    frame = self._connector_frame(at, "spur_gear", name)
    m, t = self._q(module, name + "_module"), self._q(int(teeth) if is_num(teeth) else teeth, name + "_teeth", unit="-")
    if t.unit != "-":
        raise SDKError("sdk", "spur_gear: teeth must be unitless (plan param unit '-'), got %r" % (t,))
    if m.value < 0.8:
        raise SDKError("sdk", "spur_gear: module %g mm < 0.8 mm does not print (nozzle 0.4)" % m.value,
                       action="use module >= 1 for FDM")
    th = self._q(thick, name + "_thick")
    ma = _deg(self, mesh_angle, name + "_mesh_angle")
    turn = 0 if mesh == "tooth" else Q(180.0 / t.value, "(180 deg / %s)" % t.expr, "deg")
    yaw = ma if is_num(turn) else (turn if is_num(ma) else Q(ma.value + turn.value, "(%s + %s)" % (ma.expr, turn.expr), "deg"))
    plane = self._local_datum(name + "_plane", frame, angles=(yaw, 0, 0)) if not (is_num(yaw) and yaw == 0) else frame
    prof = self.doc.addObject("Part::Part2DObjectPython", self._new_name(name + "_profile", "profile"))
    InvoluteGearFeature._InvoluteGear(prof)
    self.body.addObject(prof)
    prof.AttachmentSupport = [(plane.obj, "")]
    prof.MapMode = "FlatFace"
    prof.PressureAngle = pressure_angle
    prof.setExpression("NumberOfTeeth", t.expr)
    prof.setExpression("Modules", m.expr)
    pad = self.body.newObject("PartDesign::Pad", self._new_name(name + "_pad", "pad"))
    pad.Profile = prof
    pad.Length = th.value
    pad.setExpression("Length", th.expr)
    made = [prof, pad]
    if bore is not None:
        made.append(self.hole(frame, at=(0, 0), d=bore_d, fit=bore, name=name + "_bore"))
    mul = lambda k, extra: Q(m.value * (t.value + extra) * k, "(%s * (%s + %s) * %s)" % (m.expr, t.expr, extra, k), "mm")
    return ParamSet({"module": m, "teeth": t, "thick": th, "pitch_d": mul(1, 0), "tip_d": mul(1, 2),
                     "root_d": mul(1, -2.5), "made": made})


def snap_hook(self, at, side, length, thick, width, overhang=1.0, ramp=30, embed=1, fit="clearance", name=None):
    """Cantilever snap fit on connector `at` (both parts declare it with the same frame at rest, the latch
    closed). Frame: z along the beam from its root (z = 0, on the host face) to the tip (z = length); the
    beam fills x = -thick..0, y = +-width / 2; the lip protrudes `overhang` toward +x at the tip: a flat
    retaining face (toward the root), a land of the profile's min wall (no knife edge to print), then the
    entry ramp `ramp` deg off z (lip height = land + overhang / tan ramp).
      side "a": the hook (beam + lip), `embed` mm into the host below z = 0.
      side "b": the catch = a window in the mating wall (which fills x >= 0) around the lip + `fit`:
                x 0..overhang + fit, z over the lip +- fit / 2, width + fit; its root-side edge holds the lip.
    Checks the bending strain 1.5 x thick x overhang / free_length^2 against the material (PETG 3 %,
    PLA 1.5 %). Returns length, thick, width, overhang, lip_h, strain; params <name>_length, _thick, ..."""
    _side("snap_hook", side)
    if fit not in self.profile["fits"]:
        raise SDKError("sdk", "snap_hook: unknown fit %r; profile fits: %s" % (fit, sorted(self.profile["fits"])))
    if not 10 <= ramp <= 60:
        raise SDKError("sdk", "snap_hook: ramp must be 10..60 deg off the beam, got %r" % (ramp,))
    name = _name(self, name, "snap_" + side, "snap")
    L, t, w = self._q(length, name + "_length"), self._q(thick, name + "_thick"), self._q(width, name + "_width")
    o = self._q(overhang, name + "_overhang")
    land = self._profile_q("min_wall", self.profile["min_wall_mm"])
    lh = self._add_param(self._unique_alias(name + "_lip_h"), o / math.tan(math.radians(ramp)) + land,
                         source="sdk: min wall + overhang / tan(%g deg)" % ramp)
    free = (L - lh).value
    if free <= 0:
        raise SDKError("sdk", "snap_hook %s: the lip (%.2f mm) is longer than the beam" % (name, lh.value), location=name)
    strain = 1.5 * t.value * o.value / free ** 2
    allowed = SNAP_STRAIN[self.material]
    if strain > allowed:
        raise SDKError("sdk", "snap_hook %s: bending strain %.1f %% > %.1f %% allowed for %s" % (
            name, strain * 100, allowed * 100, self.material), location=name,
            action="lengthen the beam (strain ~ thick x overhang / length^2), thin it, or reduce the overhang")
    frame = self._connector_frame(at, "snap_hook", name)
    if side == "a":
        # sketch plane = the frame's x-z plane (normal +y): sketch (u, v) = (x, -z); pad symmetric in y
        xz = self._local_datum(name + "_plane", frame, angles=(0, 0, -90))
        e = self._q(embed, name + "_embed")
        pts = [(-t, e), (0, e), (0, -(L - lh)), (o, -(L - lh)), (o, -(L - lh + land)), (0, -L), (-t, -L)]
        s = self.sketch(name + "_sketch", on=xz).polyline(pts, name="hook")
        made = self.pad(s, w, symmetric=True, name=name + "_hook")
    else:
        c = self._profile_q("fit_" + fit, self.profile["fits"][fit])
        # sketch plane normal -x through the frame origin: sketch (u, v) = (z, y); the pocket cuts toward +x
        yz = self._local_datum(name + "_plane", frame, angles=(0, -90, 0))
        s = self.sketch(name + "_sketch", on=yz).rect(lh + c, w + c, center=(L - lh / 2, 0), name="window")
        made = self.pocket(s, depth=o + c, name=name + "_window")
    return ParamSet({"length": L, "thick": t, "width": w, "overhang": o, "lip_h": lh, "strain": strain, "made": made})


Part.hinge = hinge
Part.slide_rail = slide_rail
Part.spur_gear = spur_gear
Part.snap_hook = snap_hook
