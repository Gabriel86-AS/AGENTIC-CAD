from agenticcad import Part


def build(P):
    """On the yaw horn and the turntable rollers: a disc, a wall holding the shoulder servo (shaft along +y, right
    above the yaw axis at sh_h) and a lug under the servo case with the 623ZZ bearing the upper arm's U bracket
    turns in (M3 screw from the bracket's idler plate through the bearing bore)."""
    part = Part("turret")
    p = part.params(disc_d=100, disc_t=5, sh_h=40, sh_y=12, sh_servo_x=-10, wall_t=8, wall_x0=-42, wall_x1=20,
                    wall_above=14, lug_y=-23, lug_t=6, lug_w=20, lug_above=10)
    part.pad(part.sketch("disc", on="XY").circle(p.disc_d), p.disc_t)

    wall_top = p.sh_h + p.wall_above
    d = part.datum("wall_plane", origin=(0, p.sh_y, 0), z=(0, -1, 0), x=(1, 0, 0))   # sketch (x, y) -> (X, Z)
    s = part.sketch("wall", on=d).rect(p.wall_x1 - p.wall_x0, wall_top - p.disc_t,
                                       center=((p.wall_x0 + p.wall_x1) / 2, (p.disc_t + wall_top) / 2))
    part.pad(s, p.wall_t)
    part.connector("sh_mount", origin=(p.sh_servo_x, p.sh_y, p.sh_h), z=(0, 1, 0), x=(1, 0, 0), label="servo_mount")
    part.mount("s_sh", at="sh_mount")

    lug_top = p.sh_h + p.lug_above
    d = part.datum("lug_plane", origin=(0, p.lug_y, 0), z=(0, -1, 0), x=(1, 0, 0))
    part.pad(part.sketch("lug", on=d).rect(p.lug_w, lug_top - p.disc_t, center=(0, (p.disc_t + lug_top) / 2)), p.lug_t)
    # bearing enters from the lug face toward the bracket: seat floor one bearing width (4 mm) inside
    part.connector("sh_bearing", origin=(0, p.lug_y - 4, p.sh_h), z=(0, 1, 0), x=(1, 0, 0), label="bearing_seat")
    part.bearing_seat("b_sh", at="sh_bearing", horizontal=True)                     # turret prints flat

    part.connector("horn_seat", origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="servo_horn_face")
    part.mate_to("s_yaw.horn", at="horn_seat")
    return part
