from agenticcad import Part


def build(P):
    """Elbow link: U bracket around the elbow servo (M3 screw from its idler plate into the upper arm's bearing),
    beam to the tool (two M3 taps in its end face). Prints on its side (-y down): the beam is flush with the
    bracket's -y edge."""
    part = Part("forearm")
    part.connector("horn_seat", origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="servo_horn_face")
    u = part.servo_u_bracket("s_el", at="horn_seat", length=30, idler="tap_m3", horizontal=True)
    p = part.params(beam_h=20, beam_t=12, tool_pitch=10, tool_depth=10)
    beam_y = (p.beam_t - u.width) / 2                                                  # flush with the -y edge
    root = part.datum("beam_root", origin=(u.bridge_x, 0, u.mid_z), rot=(0, 90, 0))   # normal +x
    part.pad(part.sketch("beam", on=root).rect(p.beam_h, p.beam_t, center=(0, beam_y)), P.fore_len - u.bridge_x)
    part.connector("tool", origin=(P.fore_len, beam_y, u.mid_z), z=(1, 0, 0), x=(0, 0, 1), label="tool")
    # tool mount: two M3 taps in the beam's end face, across its height
    tip = part.datum("tip_face", origin=(P.fore_len, 0, u.mid_z), rot=(0, 90, 0))      # sketch (x, y) -> (-z, y)
    part.hole(tip, at=[(p.tool_pitch / 2, beam_y), (-p.tool_pitch / 2, beam_y)], fit="tap_m3", depth=p.tool_depth,
              name="tool_mount")
    return part
