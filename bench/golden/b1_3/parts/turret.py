from agenticcad import Part


def build(P):
    """On the yaw horn: base plate, a wall holding the shoulder servo (shaft along +y, right above the yaw
    axis at sh_h) and a lug under the servo case carrying the M3 idler screw for the arm's U bracket."""
    part = Part("turret")
    p = part.params(base_l=70, base_w=70, base_cx=-10, base_t=5, sh_h=40, sh_y=12, sh_servo_x=-10,
                    wall_t=8, wall_x0=-42, wall_x1=20, wall_above=14, lug_y=-23, lug_t=6, lug_w=20, lug_above=8)
    part.pad(part.sketch("base", on="XY").rect(p.base_l, p.base_w, center=(p.base_cx, 0)), p.base_t)

    wall_top = p.sh_h + p.wall_above
    d = part.datum("wall_plane", origin=(0, p.sh_y, 0), z=(0, -1, 0), x=(1, 0, 0))   # sketch (x, y) -> (X, Z)
    s = part.sketch("wall", on=d).rect(p.wall_x1 - p.wall_x0, wall_top - p.base_t,
                                       center=((p.wall_x0 + p.wall_x1) / 2, (p.base_t + wall_top) / 2))
    part.pad(s, p.wall_t)
    part.connector("sh_mount", origin=(p.sh_servo_x, p.sh_y, p.sh_h), z=(0, 1, 0), x=(1, 0, 0), label="servo_mount")
    part.mount("s_sh", at="sh_mount")

    lug_top = p.sh_h + p.lug_above
    d = part.datum("lug_plane", origin=(0, p.lug_y, 0), z=(0, -1, 0), x=(1, 0, 0))
    part.pad(part.sketch("lug", on=d).rect(p.lug_w, lug_top - p.base_t, center=(0, (p.base_t + lug_top) / 2)), p.lug_t)
    part.hole(d, at=(0, p.sh_h), fit="tap_m3", name="idler_screw")

    part.connector("horn_seat", origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="servo_horn_face")
    part.mate_to("s_yaw.horn", at="horn_seat")
    return part
