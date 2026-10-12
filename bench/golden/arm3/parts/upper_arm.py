from agenticcad import Part


def build(P):
    """Shoulder link: U bracket around the shoulder servo, beam to the elbow, where a spine joins a wall holding the
    elbow servo (shaft along +z, parallel to the shoulder, at x = upper_len) and a lug below the forearm's idler
    plate with the 623ZZ bearing. Prints on its side (-y down): everything is flush with the bracket's -y edge."""
    part = Part("upper_arm")
    part.connector("horn_seat", origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="servo_horn_face")
    u = part.servo_u_bracket("s_sh", at="horn_seat", length=30, idler="tap_m3", horizontal=True)
    p = part.params(beam_h=20, beam_t=12, el_z=-12, wall_t=8, wall_x1=20, spine_back=42, spine_t=8,
                    lug_down=35, lug_t=6, lug_x1=10, lug_y1=9)
    edge = -u.width / 2                                                               # the -y face everything sits on
    spine_x0 = P.upper_len - p.spine_back - p.spine_t

    # beam from the bridge's outer face to the spine, its top flush with the elbow wall's top
    root = part.datum("beam_root", origin=(u.bridge_x, 0, p.el_z - p.beam_h / 2), rot=(0, 90, 0))   # normal +x
    part.pad(part.sketch("beam", on=root).rect(p.beam_h, p.beam_t, center=(0, edge + p.beam_t / 2)),
             spine_x0 - u.bridge_x)

    # spine: from the lug's bottom to the wall's top, behind the elbow servo flange
    lug_z1 = p.el_z - p.lug_down                                                     # lug face toward the forearm
    lug_z0 = lug_z1 - p.lug_t
    d = part.datum("spine_plane", origin=(spine_x0, 0, 0), rot=(0, 90, 0))            # sketch (x, y) -> (-Z, Y)
    part.pad(part.sketch("spine", on=d).rect(p.el_z - lug_z0, u.width, center=(-(p.el_z + lug_z0) / 2, 0)), p.spine_t)

    # wall the elbow servo flange rests on (top at el_z), shaft at x = upper_len
    el_x = P.upper_len
    wall_x0 = spine_x0 + p.spine_t
    d = part.datum("wall_plane", plane="XY", offset=p.el_z - p.wall_t)
    part.pad(part.sketch("wall", on=d).rect(el_x + p.wall_x1 - wall_x0, u.width,
                                            center=((wall_x0 + el_x + p.wall_x1) / 2, 0)), p.wall_t)
    part.connector("el_mount", origin=(el_x - 10, 0, p.el_z), z=(0, 0, 1), x=(1, 0, 0), label="servo_mount")
    part.mount("s_el", at="el_mount")

    # lug under the forearm bracket's idler plate; bearing enters from its top face, floor 4 mm inside
    d = part.datum("lug_plane", plane="XY", offset=lug_z0)
    part.pad(part.sketch("lug", on=d).rect(el_x + p.lug_x1 - wall_x0, p.lug_y1 - edge,
                                           center=((wall_x0 + el_x + p.lug_x1) / 2, (edge + p.lug_y1) / 2)), p.lug_t)
    part.connector("el_bearing", origin=(el_x, 0, lug_z1 - 4), z=(0, 0, 1), x=(1, 0, 0), label="bearing_seat")
    part.bearing_seat("b_el", at="el_bearing", horizontal=True)
    return part
