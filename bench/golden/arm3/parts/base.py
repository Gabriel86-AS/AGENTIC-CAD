from agenticcad import Part


def build(P):
    """Ground: plate + roller turntable around the yaw servo (shaft on the part z axis, the turret rides on three
    623ZZ rollers level with the horn face) + electronics bay behind it (the arm sweeps the +x half only),
    turned so the plate fits the build volume."""
    part = Part("base")
    # yaw_h = case below the flange + clearance / 2 + turntable floor: the turntable's bottom is the plate's, so it
    # follows the card, not the user
    p = part.params(plate_l=176, plate_w=110, plate_cx=-35, thick=5, yaw_servo_x=-10, bay_x=-87, tie_in=8,
                    yaw_h={"value": 29.3, "min": 29.3, "max": 29.3})
    part.pad(part.sketch("plate", on="XY").rect(p.plate_l, p.plate_w, center=(p.plate_cx, 0)), p.thick)
    # tie-down: an M3 screw at each corner, heads sunk in the plate top (the arm cantilevers 200 mm past the axis)
    top = part.datum("plate_top", plane="XY", offset=p.thick)
    hx, hy = p.plate_l / 2 - p.tie_in, p.plate_w / 2 - p.tie_in
    part.hole(top, at=[(p.plate_cx + hx, hy), (p.plate_cx + hx, -hy), (p.plate_cx - hx, hy), (p.plate_cx - hx, -hy)],
              fit="clearance_m3", counterbore=True, name="tie_down")
    part.connector("servo_mount", origin=(p.yaw_servo_x, 0, p.yaw_h), z=(0, 0, 1), x=(1, 0, 0), label="servo_mount")
    part.turntable_base("s_yaw", at="servo_mount")
    # bay frame x toward +y: its 85 mm length runs across the plate
    part.connector("ebay_mount", origin=(p.bay_x, 0, p.thick), z=(0, 0, 1), x=(0, 1, 0), label="ebay_mount")
    part.electronics_bay("ebay", at="ebay_mount", floor=p.thick)
    return part
