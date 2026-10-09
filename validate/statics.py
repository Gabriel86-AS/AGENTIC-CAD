"""Static gravity load at the joints (gate L6). Pure Python 3.11.

For q-dot = q-ddot = 0, RNEA reduces to the gravity term: the load on joint i is the moment (revolute)
or force (prismatic) of the weight of everything downstream of it, projected on the joint axis:
    tau_i = z_i . sum_k (c_k - p_i) x (m_k g),   f_i = z_i . sum_k m_k g
with p_i, z_i the joint frame (world . Cp . M(q)) origin and axis. Pinocchio's RNEA gives the same
numbers (cross-checked in the description check D2); this version runs without it. World +z is up.
Units: masses kg, positions mm, results N.m (rotary) and N (linear).
"""
from assembly.kinematics import LINEAR, ROTARY, axis, mul, origin

G = 9.81


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _apply(t, p):
    return [sum(t[i][k] * p[k] for k in range(3)) + t[i][3] for i in range(3)]


def subtree(tree):
    """owner -> set of owners at or below it."""
    children = {}
    for e in tree.edges:
        children.setdefault(e.parent, []).append(e.child)
    out = {}

    def walk(o):
        if o not in out:
            s = {o}
            for c in children.get(o, []):
                s |= walk(c)
            out[o] = s
        return out[o]

    walk(tree.ground)
    return out


def joint_loads(tree, values, masses, below=None, world=None):
    """{joint: load} for the moving joints. `masses`: [(owner, kg, com_mm in the owner frame)], point
    masses included (payload). Signed: the actuator must supply -load to hold the pose."""
    world = world or tree.fk(values)
    below = below or subtree(tree)
    pts = [(o, m, _apply(world[o], c)) for o, m, c in masses if o in world]
    out = {}
    for e in tree.edges:
        if e.kind != "joint" or e.jtype not in ROTARY | LINEAR:
            continue
        jf = mul(mul(world[e.parent], tree.frames[e.parent][e.pconn]), tree.edge_motion(e, values))
        p, z = origin(jf), axis(jf, 2)
        down = below[e.child]
        if e.jtype in LINEAR:
            out[e.name] = sum(-m * G * z[2] for o, m, _ in pts if o in down)
        else:
            tau = 0.0
            for o, m, c in pts:
                if o in down:
                    r = [(c[i] - p[i]) / 1000.0 for i in range(3)]
                    tau += sum(a * b for a, b in zip(_cross(r, [0.0, 0.0, -m * G]), z))
            out[e.name] = tau
    return out
