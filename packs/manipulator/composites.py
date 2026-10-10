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
from sdk.part import Part, ParamSet


def servo_u_bracket(self, servo, at, length=None, width=30, thick=5, gap=2, idler="clearance_m3", idler_d=None,
                    access=True, name=None):
    """U bracket driven by `servo` ("<component>"): plate A on its horn at connector `at`, plate B beyond the
    case bottom with an idler bore on the axis, joined by a bridge at x = length (axis -> bridge inner face).
    length default: clears the flange corner in the swept half-plane + 3 mm. Returns a ParamSet (frame of
    `at`): length, width, thick, gap, bridge_x (outer face x), plate_b_z (outer face z, < 0), mid_z (bridge
    centre z). Params: <name>_length, _width, _thick, _gap; card dims as <servo>_<param>."""
    card, prefix = self._card(servo)
    if card.get("kind") != "actuator.servo":
        raise SDKError("sdk", "servo_u_bracket: %s is a %s, not a servo" % (card["id"], card.get("kind")))
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
    made.append(self.hole(out_b, at=[(0, 0)], d=idler_d, fit=idler, depth=thick, name=name + "_idler"))
    made += self.mate_to(servo + ".horn", at, access=access, name=name + "_horn")
    return ParamSet({"length": length, "width": width, "thick": thick, "gap": gap, "bridge_x": length + thick,
                     "plate_b_z": b_top - thick, "mid_z": mid_z, "made": made})


Part.servo_u_bracket = servo_u_bracket
