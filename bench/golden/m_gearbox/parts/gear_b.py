from agenticcad import Part


def build(P):
    """Follower gear (30 teeth, module 1.5); a tooth gap faces gear_a (-x) at rest."""
    part = Part("gear_b")
    part.connector("axle", origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="axle")
    part.spur_gear("axle", 1.5, P.teeth_b, 6, mesh="gap", mesh_angle=180)
    return part
