from agenticcad import Part


def build(P):
    """Shoulder link: U bracket around the shoulder servo (horn plate + idler plate), beam to the tool."""
    part = Part("arm")
    part.connector("horn_seat", origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="servo_horn_face")
    u = part.servo_u_bracket("s_sh", at="horn_seat", length=30)
    p = part.params(beam_h=20, beam_t=12)
    root = part.datum("beam_root", origin=(u.bridge_x, 0, u.mid_z), rot=(0, 90, 0))   # normal +x
    part.pad(part.sketch("beam", on=root).rect(p.beam_h, p.beam_t), P.arm_len - u.bridge_x)
    part.connector("tool", origin=(P.arm_len, 0, u.mid_z), z=(1, 0, 0), x=(0, 0, 1), label="tool")
    return part
