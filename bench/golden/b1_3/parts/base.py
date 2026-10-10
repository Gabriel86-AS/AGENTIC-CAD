from agenticcad import Part


def build(P):
    """Ground: plate + tower holding the yaw servo (shaft on the part z axis) + electronics bay toward +x."""
    part = Part("base")
    p = part.params(plate_l=170, plate_w=80, plate_cx=35, thick=5, yaw_servo_x=-10, tower_l=60, tower_w=32,
                    tower_h=36, bay_x=75)
    part.pad(part.sketch("plate", on="XY").rect(p.plate_l, p.plate_w, center=(p.plate_cx, 0)), p.thick)
    part.pad(part.sketch("tower", on="XY").rect(p.tower_l, p.tower_w, center=(p.yaw_servo_x, 0)), p.tower_h)
    part.connector("servo_mount", origin=(p.yaw_servo_x, 0, p.tower_h), z=(0, 0, 1), x=(1, 0, 0), label="servo_mount")
    part.mount("s_yaw", at="servo_mount")
    # bay frame x toward -x: its cable exit faces the tower
    part.connector("ebay_mount", origin=(p.bay_x, 0, p.thick), z=(0, 0, 1), x=(-1, 0, 0), label="ebay_mount")
    part.electronics_bay("ebay", at="ebay_mount", floor=p.thick)
    return part
