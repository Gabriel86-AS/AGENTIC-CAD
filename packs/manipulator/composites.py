"""Manipulator pack composites (plan §3.3 rule 9), registered on sdk.Part. Run inside freecadcmd.

servo_u_bracket: the link a standard servo drives through its horn, supported on both sides of the servo
so the output shaft is not the only bearing. All geometry is in the frame of connector `at`, which must
coincide with the servo's `horn` connector in the assembly (z = shaft axis, from the servo into the part;
origin on the horn disc underside):

            z
     plate A (horn side, z 0..t): horn recess + M2 taps + access hole (mate_to)
     |==========================|  bridge (x = length .. length + t)
     |   [ servo case, fixed ]  |
     |==========================|
     plate B (idler side): below the case bottom by `gap`; idler bore on the shaft axis
      -> x (bracket direction)

The idler bore takes a pin or screw axle (fit "clearance_m3" by default; "press" + d for a bearing)
fixed to the part that holds the servo, coaxial with the shaft. The plan should not declare it as a joint
(no closed loops, D10); gate L5 checks the swept clearance.
"""
import math

from sdk.errors import SDKError
from sdk.part import Datum, Part, ParamSet


def servo_u_bracket(self, servo, at, length=None, width=30, thick=5, gap=2, idler="clearance_m3", idler_d=None,
                    access=True, horizontal=False, name=None):
    """U bracket driven by `servo` ("<component>"): plate A on its horn at connector `at`, plate B beyond the
    case bottom with an idler bore on the axis, joined by a bridge at x = length (axis -> bridge inner face).
    length default: clears the flange corner in the swept half-plane + 3 mm. horizontal=True: the bracket prints
    on its side (idler bore axis horizontal), so the bore gets the profile's horizontal allowance. Returns a ParamSet (frame of
    `at`): length, width, thick, gap, bridge_x (outer face x), plate_b_z (outer face z, < 0), mid_z (bridge
    centre z). Params: <name>_length, _width, _thick, _gap; card dims as <servo>_<param>."""
    card, prefix = _servo(self, servo, "servo_u_bracket")
    horn = (card.get("connectors") or {}).get("horn")
    if not horn:
        raise SDKError("sdk", "servo_u_bracket: card %s has no horn connector" % card["id"])
    name = self._new_name(name or "ubracket_" + prefix, "ubracket")
    cq = lambda v: self._card_q(card, prefix, v)
    shaft_x, horn_z = cq(horn["o"][0]), cq(horn["o"][2])
    if length is None:
        fl, bw = card["params_mm"]["flange_l"], card["params_mm"]["body_w"]
        reach = math.hypot(fl / 2 - float(shaft_x), bw / 2)        # flange corner seen from the shaft axis
        length = self._q(round(reach + 3.0, 2), name + "_length", source="sdk: flange corner radius + 3 mm")
    else:
        length = self._q(length, name + "_length")
    width, thick, gap = self._q(width, name + "_width"), self._q(thick, name + "_thick"), self._q(gap, name + "_gap")
    below = horn_z + cq("body_below")                                # horn face -> case bottom
    b_top = -(below + gap)                                           # plate B inner face (z)
    frame = self._connector_frame(at, "servo_u_bracket", name)

    made = []
    for tag, base, reverse in (("a", frame, False), ("b", self._local_datum(name + "_b_frame", frame, (0, 0, b_top)), True)):
        s = self.sketch("%s_%s_disc_sketch" % (name, tag), on=base).circle(width, name="disc")
        made.append(self.pad(s, thick, reverse=reverse, name="%s_%s_disc" % (name, tag)))
        s = self.sketch("%s_%s_bar_sketch" % (name, tag), on=base).rect(length + thick, width, center=((length + thick) / 2, 0), name="bar")
        made.append(self.pad(s, thick, reverse=reverse, name="%s_%s_bar" % (name, tag)))
    # bridge: sketch normal +x (pitch 90: z -> +x, sketch x -> -z), spans plate B outer face .. plate A top
    mid_z = b_top / 2                                                # plate A top (thick) .. plate B bottom
    span = thick * 2 + below + gap
    d = self._local_datum(name + "_bridge_frame", frame, (length, 0, mid_z), (0, 90, 0))
    s = self.sketch(name + "_bridge_sketch", on=d).rect(span, width, name="bridge")
    made.append(self.pad(s, thick, name=name + "_bridge"))
    # idler bore from plate B's outer face (datum normal -z, pointing out of the part)
    out_b = self._local_datum(name + "_idler_frame", frame, (0, 0, b_top - thick), (0, 0, 180))
    made.append(self.hole(out_b, at=[(0, 0)], d=idler_d, fit=idler, depth=thick, horizontal=horizontal,
                          name=name + "_idler"))
    made += self.mate_to(servo + ".horn", at, access=access, name=name + "_horn")
    return ParamSet({"length": length, "width": width, "thick": thick, "gap": gap, "bridge_x": length + thick,
                     "plate_b_z": b_top - thick, "mid_z": mid_z, "made": made})


def _servo(self, servo, call):
    card, prefix = self._card(servo)
    if card.get("kind") != "actuator.servo":
        raise SDKError("sdk", "%s: %s is a %s, not a servo" % (call, card["id"], card.get("kind")))
    return card, prefix


def bearing_seat(self, bearing, at, through=True, fit="press", horizontal=False, name=None):
    """Seat for a ball bearing (plan component id or card id, e.g. "bearing/608zz") whose card frame sits on
    connector `at`: seat floor, z out of the floor through the bearing. Bore outer_d + `fit` allowance (press by
    default; horizontal adds the profile's extra), `width` deep from z = width down to the floor; with `through`,
    a lip hole of the card's housing-shoulder diameter on through the part (shaft / screw passage).
    Put `at` one bearing width below the face the bearing enters from. Returns outer_d, depth, lip_d, bore_d."""
    card, prefix = self._card(bearing)
    if not str(card.get("kind", "")).startswith("bearing"):
        raise SDKError("sdk", "bearing_seat: %s is a %s, not a bearing" % (card["id"], card.get("kind")))
    if fit not in self.profile["fits"]:
        raise SDKError("sdk", "bearing_seat: unknown fit %r; profile fits: %s" % (fit, sorted(self.profile["fits"])))
    name = self._new_name(name or "seat_" + prefix, "seat")
    frame = self._connector_frame(at, "bearing_seat", name)
    cq = lambda v: self._card_q(card, prefix, v)
    seat = card["interfaces"]["seat"]
    od = cq(seat["outer_d"]) + self._profile_q("fit_" + fit, self.profile["fits"][fit])
    if horizontal:
        od = od + self._profile_q("horizontal_hole_extra", self.profile["horizontal_hole_extra_mm"])
    depth, lip = cq(seat["depth"]), cq(seat["lip_d"])
    mouth = self._local_datum(name + "_mouth", frame, (0, 0, depth))
    made = [self.pocket(self.sketch(name + "_bore_sketch", on=mouth).circle(od, name="outer"), depth=depth,
                        name=name + "_bore")]
    if through:
        made.append(self.pocket(self.sketch(name + "_lip_sketch", on=frame).circle(lip, name="lip"), through=True,
                                name=name + "_lip"))
    return ParamSet({"outer_d": od, "depth": depth, "lip_d": lip, "bore_d": cq(card["interfaces"]["bore"]["d"]),
                     "made": made})


def horn_coupler(self, servo, at, wall=3, thick=None, access=True, name=None):
    """Round hub for a part driven by `servo`'s disc horn at connector `at` (as mate_to: z from the servo into
    the part, part face at z = 0): a disc of horn disc + clearance + 2 x wall padded from z = 0 to `thick`
    (default: horn recess + horn screw depth + min wall), then the horn recess, M2 taps and access hole.
    Lets a link narrower than the horn take it. Returns hub_d, thick; params <name>_hub_d, _thick, _wall."""
    card, prefix = _servo(self, servo, "horn_coupler")
    hz = next((v for v in card.get("interfaces", {}).values() if isinstance(v, dict) and v.get("kind") == "disc_horn"), None)
    if hz is None:
        raise SDKError("sdk", "horn_coupler: card %s has no disc horn interface" % card["id"])
    name = self._new_name(name or "coupler_" + prefix, "coupler")
    frame = self._connector_frame(at, "horn_coupler", name)
    cq = lambda v: self._card_q(card, prefix, v)
    clr = self._profile_q("fit_clearance", self.profile["fits"]["clearance"])
    wall = self._q(wall, name + "_wall")
    hub_d = self._add_param(self._unique_alias(name + "_hub_d"), cq(hz["disc_d"]) + clr + wall * 2,
                            source="sdk: horn disc + clearance + 2 x wall")
    if thick is None:
        min_wall = self._profile_q("min_wall", self.profile["min_wall_mm"])
        thick = self._add_param(self._unique_alias(name + "_thick"),
                                cq(hz["disc_t"]) + clr / 2 + self._screw_depth(hz["screw"], prefix + "_horn") + min_wall,
                                source="sdk: horn recess + screw depth + min wall")
    else:
        thick = self._q(thick, name + "_thick")
    made = [self.pad(self.sketch(name + "_hub_sketch", on=frame).circle(hub_d, name="hub"), thick, name=name + "_hub")]
    made += self.mate_to(servo + ".horn", at, access=access, name=name + "_horn")
    return ParamSet({"hub_d": hub_d, "thick": thick, "wall": wall, "made": made})


def link_bar(self, on, length, width, thick, end_holes=None, hole_d=None, reverse=False, name=None):
    """Straight link with round ends in the sketch plane of `on` (origin plane or Datum; usually a frame
    datum at the joint): from (0, 0) to (length, 0), `width` wide, `thick` along the normal (`reverse`:
    against it). end_holes = a hole fit at both end centres ("clearance_m3", "tap_m3", "press" + hole_d, ...).
    Returns length, width, thick; params <name>_length, _width, _thick."""
    return ParamSet(_bar(self, self._new_name(name, "link"), on, length, width, thick, end_holes, hole_d, reverse))


def _bar(self, name, on, length, width, thick, end_holes, hole_d, reverse):
    L, w, t = self._q(length, name + "_length"), self._q(width, name + "_width"), self._q(thick, name + "_thick")
    s = self.sketch(name + "_sketch", on=on).slot((0, 0), (L, 0), w, name="bar")
    made = [self.pad(s, t, reverse=reverse, name=name + "_pad")]
    if end_holes:
        made.append(self.hole(on, at=[(0, 0), (L, 0)], d=hole_d, fit=end_holes, name=name + "_holes"))
    return {"length": L, "width": w, "thick": t, "made": made}


def hollow_link(self, on, length, width, thick, wall=2, end_solid=None, end_holes=None, hole_d=None, reverse=False,
                name=None):
    """link_bar with an open cable trough in its far face (prints without supports; cables lie in it): a slot
    (width - 2 x wall) from x = end_solid to length - end_solid, thick - wall deep. end_solid (default = width)
    keeps the joint ends solid. Returns the link_bar values + wall, end_solid; params <name>_wall, _end_solid."""
    name = self._new_name(name, "link")
    bar = _bar(self, name, on, length, width, thick, end_holes, hole_d, reverse)
    L, w, t = bar["length"], bar["width"], bar["thick"]
    wall = self._q(wall, name + "_wall")
    end = w if end_solid is None else self._q(end_solid, name + "_end_solid")
    if (L - end * 2).value <= 0 or (w - wall * 2).value <= 0 or (t - wall).value <= 0:
        raise SDKError("sdk", "hollow_link %s: no room for the trough (needs length > 2 x end_solid, width > 2 x wall, "
                              "thick > wall)" % name, location=name)
    far = -t if reverse else t
    face = (self._local_datum(name + "_far", on, (0, 0, far)) if isinstance(on, Datum)
            else self.datum(name + "_far", plane=on, offset=far))
    s = self.sketch(name + "_trough_sketch", on=face).slot((end, 0), (L - end, 0), w - wall * 2, name="trough")
    bar["made"].append(self.pocket(s, depth=t - wall, reverse=reverse, name=name + "_trough"))
    return ParamSet(dict(bar, wall=wall, end_solid=end))


def turntable_base(self, servo, at, bearing="bearing/623zz", base_d=None, rollers=3, protrude=1, floor=3, wall=3,
                   name=None):
    """Base of a yaw joint: `servo` stands in it shaft up, card frame on connector `at` (flange underside,
    z up), and the turret it drives (mate_to / horn_coupler on its horn) also rests on `rollers` ball bearings
    (`bearing` card, radial M3-type axles) whose tops are level with the horn face, so tipping loads go
    into the base instead of the servo shaft. Builds, on the shaft axis: a cylinder base_d wide from `floor`
    below the case to the ring top (horn face - protrude), the servo pocket + taps + cable keep-out (mount),
    an open well for the flange and case top, and the roller pockets with their axle holes. The turret's
    underside must cover radius roller_r +- width/2. Returns base_d, roller_r, top_z, horn_z, axle_z,
    bottom_z, axis_x (frame of `at`); params <name>_base_d, _roller_r, _protrude, _floor_t, _wall."""
    card, prefix = _servo(self, servo, "turntable_base")
    bcard, bprefix = self._card(bearing)
    if not str(bcard.get("kind", "")).startswith("bearing"):
        raise SDKError("sdk", "turntable_base: %s is a %s, not a bearing" % (bcard["id"], bcard.get("kind")))
    if not isinstance(rollers, int) or rollers < 3:
        raise SDKError("sdk", "turntable_base: rollers must be an int >= 3, got %r" % (rollers,))
    horn = (card.get("connectors") or {}).get("horn")
    if not horn:
        raise SDKError("sdk", "turntable_base: card %s has no horn connector" % card["id"])
    name = self._new_name(name or "turntable_" + prefix, "turntable")
    cq = lambda v: self._card_q(card, prefix, v)
    bq = lambda v: self._card_q(bcard, bprefix, v)
    clr = self._profile_q("fit_clearance", self.profile["fits"]["clearance"])
    shaft_x, horn_z = cq(horn["o"][0]), cq(horn["o"][2])
    D, B, bore = bq("outer_d"), bq("width"), bq("bore_d")
    protrude, floor_t, wall = (self._q(protrude, name + "_protrude"), self._q(floor, name + "_floor_t"),
                               self._q(wall, name + "_wall"))
    pm = card["params_mm"]
    reach = math.hypot(pm["flange_l"] / 2 + abs(float(shaft_x)), pm["body_w"] / 2)   # far flange corner from the axis
    roller_r = self._q(round(reach + float(clr) + float(wall) + float(B) / 2, 2), name + "_roller_r",
                       source="sdk: flange corner radius + clearance + wall + half roller")
    if base_d is None:
        base_d = self._add_param(self._unique_alias(name + "_base_d"), (roller_r + wall) * 2 + B + clr,
                                 source="sdk: rollers + wall")
    else:
        base_d = self._q(base_d, name + "_base_d")
    top = horn_z - protrude                                   # ring top, frame z
    bottom = -(cq("body_below") + clr / 2 + floor_t)          # under the mount pocket
    axle_z = horn_z - D / 2                                   # roller tops level with the horn face
    frame = self._connector_frame(at, "turntable_base", name)
    axis = self._local_datum(name + "_axis", frame, (shaft_x, 0, 0))
    bot = self._local_datum(name + "_bottom", axis, (0, 0, bottom))
    made = [self.pad(self.sketch(name + "_body_sketch", on=bot).circle(base_d, name="body"), top - bottom,
                     name=name + "_body")]
    made += self.mount(servo, at, name=name + "_servo")
    well_top = self._local_datum(name + "_well_top", frame, (0, 0, top))
    s = self.sketch(name + "_well_sketch", on=well_top).rect(cq("flange_l") + clr * 2, cq("body_w") + clr * 2, name="well")
    made.append(self.pocket(s, depth=top, name=name + "_well"))
    engage = self._q(4, name + "_axle_engage")
    for k in range(rollers):
        rd = self._local_datum("%s_roller%d_frame" % (name, k), axis, angles=(round(360.0 * k / rollers, 9), 0, 0))
        rt = self._local_datum("%s_roller%d_top" % (name, k), rd, (0, 0, top))
        s = self.sketch("%s_roller%d_sketch" % (name, k), on=rt).rect(B + clr, D + clr, center=(roller_r, 0), name="roller")
        made.append(self.pocket(s, depth=D + clr - protrude, name="%s_roller%d" % (name, k)))
        ax = self._local_datum("%s_axle%d_frame" % (name, k), rd, (base_d / 2, 0, axle_z), (0, 90, 0))  # z radial out
        made.append(self.hole(ax, at=(0, 0), d=bore, fit="close", horizontal=True,
                              depth=base_d / 2 - roller_r + B / 2 + clr / 2 + engage, name="%s_axle%d" % (name, k)))
    return ParamSet({"base_d": base_d, "roller_r": roller_r, "top_z": top, "horn_z": horn_z, "axle_z": axle_z,
                     "bottom_z": bottom, "axis_x": shaft_x, "made": made})


Part.servo_u_bracket = servo_u_bracket
Part.bearing_seat = bearing_seat
Part.horn_coupler = horn_coupler
Part.link_bar = link_bar
Part.hollow_link = hollow_link
Part.turntable_base = turntable_base
