from agenticcad import Part


def build(P):
    """Ground plate: washer bosses tapped M3 for the two gear axles, the fixed half of the cover hinge along
    the back edge, two tapped M3 holes for the catch block. The plate's back and front edges
    follow the hinge axis and the catch, so every feature stays on it when a dimension changes."""
    part = Part("base")
    p = part.params(plate_w=90, plate_cx=25, thick=4, boss_d=8, boss_h=1, gear_lift=0.5, gear_cd=31.6,
                    hinge_y=40, hinge_z=4.5, knuckle_r=4, catch_x=27, catch_y=-35.5, catch_t=6, rim=0.5, screw_x=9)
    y_back = p.hinge_y + p.knuckle_r
    y_front = p.catch_y - p.catch_t - p.rim
    bottom = part.datum("bottom", plane="XY", offset=-p.thick)
    part.pad(part.sketch("plate", on=bottom).rect(p.plate_w, y_back - y_front, center=(p.plate_cx, (y_back + y_front) / 2)),
             p.thick)
    part.boss("XY", at=[(0, 0), (p.gear_cd, 0)], d=p.boss_d, height=p.boss_h, hole="tap_m3",
              hole_depth=p.boss_h + p.thick, name="axle_boss")
    part.connector("axle_a", origin=(0, 0, p.boss_h + p.gear_lift), z=(0, 0, 1), x=(1, 0, 0), label="axle")
    part.connector("axle_b", origin=(p.gear_cd, 0, p.boss_h + p.gear_lift), z=(0, 0, 1), x=(1, 0, 0), label="axle")
    # hinge axis along -x (the cover opens with positive q), above the plate, behind the cover's back wall;
    # the webs drop from the axis into the plate
    part.connector("hinge", origin=(60, p.hinge_y, p.hinge_z), z=(-1, 0, 0), x=(0, 0, 1), label="hinge")
    part.hinge("hinge", "a", 70, knuckles=5, od=p.knuckle_r * 2, pin="m3", web=p.hinge_z, web_angle=180,
               horizontal=True)
    part.connector("catch_mount", origin=(p.catch_x, p.catch_y, 0), z=(0, 0, 1), x=(1, 0, 0), label="catch_mount")
    # the catch block's two M3 screws come from its top into the plate
    sy = p.catch_y - p.catch_t / 2
    part.hole("XY", at=[(p.catch_x - p.screw_x, sy), (p.catch_x + p.screw_x, sy)], fit="tap_m3", depth=p.thick,
              name="catch_screws")
    return part
