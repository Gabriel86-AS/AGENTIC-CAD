from agenticcad import Part


def build(P):
    """Driver gear (12 teeth, module 1.5) with a crank knob; a tooth points at gear_b (+x) at rest."""
    part = Part("gear_a")
    part.connector("axle", origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="axle")
    g = part.spur_gear("axle", 1.5, P.teeth_a, 6, mesh="tooth", mesh_angle=0)
    p = part.params(knob_r=4, knob_d=5, knob_h=P.knob_h)
    top = part.datum("top", plane="XY", offset=g.thick)
    part.boss(top, at=(p.knob_r, 0), d=p.knob_d, height=p.knob_h, name="knob")
    return part
