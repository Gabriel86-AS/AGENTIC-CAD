"""Kinematic tree and forward kinematics of an AssemblyPlan (P1 T1.6, mating rule D13).

Pure Python 3.11, no third-party imports: the orchestrator (plan checks, exporter) and the FreeCAD
worker (assembly writer) share it. Frames are 4x4 row-major nested lists, mm.

Owners are parts and component instances. Edges, each from a parent owner to a child owner:
  attach   component `c` fixed to `part.conn`: card frame (= its mount connector, D22) on the connector
  joint    `parent "<owner>.<conn>"` -> `child "<owner>.<conn>"`; at q = 0 the two connector frames
           coincide, so  T_child_owner(q) = T_parent_owner . Cp . M(q) . Cc^-1  (D13)
M(q): revolute / continuous / gears = Rz(q) [rad]; slider / rack_pinion = Tz(q) [mm];
cylindrical = Rz(q) (its translation stays 0 in P1); fixed / ball = identity.
Coupled joints (gears, rack_pinion, or any joint with `mimic`) take q = multiplier * q_ref + offset.
Problems come back as worker-format signal dicts (gate L5), never as exceptions.
"""
import math

GATE = "L5"
ROTARY = {"revolute", "continuous", "gears", "cylindrical"}
LINEAR = {"slider", "rack_pinion"}


# ---- 4x4 frames -----------------------------------------------------------------------------
def identity():
    return [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]


def mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def inv(t):
    """Inverse of a rigid transform."""
    r = [[t[j][i] for j in range(3)] for i in range(3)]
    p = [-sum(r[i][k] * t[k][3] for k in range(3)) for i in range(3)]
    return [r[0] + [p[0]], r[1] + [p[1]], r[2] + [p[2]], [0.0, 0.0, 0.0, 1.0]]


def frame(o, z, x):
    """Connector frame (o, z, x) -> 4x4; y = z x x. Columns are the frame axes."""
    y = (z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0])
    return [[x[0], y[0], z[0], o[0]], [x[1], y[1], z[1], o[1]], [x[2], y[2], z[2], o[2]], [0.0, 0.0, 0.0, 1.0]]


def rz(q):
    c, s = math.cos(q), math.sin(q)
    return [[c, -s, 0.0, 0.0], [s, c, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]


def tz(d):
    t = identity()
    t[2][3] = float(d)
    return t


def motion(jtype, q):
    if jtype in ROTARY:
        return rz(q)
    if jtype in LINEAR:
        return tz(q)
    return identity()


def origin(t):
    return [t[0][3], t[1][3], t[2][3]]


def axis(t, i):
    return [t[0][i], t[1][i], t[2][i]]


def residual(a, b):
    """(position error mm, rotation error deg) between two frames."""
    pos = math.sqrt(sum((x - y) ** 2 for x, y in zip(origin(a), origin(b))))
    r = mul(inv(a), b)
    c = max(-1.0, min(1.0, (r[0][0] + r[1][1] + r[2][2] - 1.0) / 2.0))
    return pos, math.degrees(math.acos(c))


# ---- tree -----------------------------------------------------------------------------------
def _signal(cause, location="", action="", level="failure"):
    return {"level": level, "gate": GATE, "cause": cause, "location": location, "action": action}


def _split(ref):
    owner, _, conn = ref.partition(".")
    return owner, conn


class Edge:
    """parent owner -> child owner. kind 'attach' (component on a part) or 'joint'."""

    def __init__(self, kind, name, parent, pconn, child, cconn, jtype="fixed", joint=None):
        self.kind, self.name = kind, name
        self.parent, self.pconn, self.child, self.cconn = parent, pconn, child, cconn
        self.jtype, self.joint = jtype, joint or {}


class Tree:
    """Kinematic tree of a plan. `frames[owner][conn]` = connector frame (4x4) in the owner frame;
    a component's card frame is its owner frame; `mounts[component]` names its mount connector (the one at
    the card frame origin, D22), else the attach edge uses the card frame itself."""

    def __init__(self, plan, frames, mounts=None):
        self.plan, self.frames, self.mounts = plan, frames, mounts or {}
        self.ground = plan["ground"]
        self.signals = []
        self.edges = []          # root -> leaf order once built
        self.parent_edge = {}    # child owner -> Edge
        self._build()

    # plan dicts are AssemblyPlan.model_dump() output
    def _owners(self):
        parts = [p["name"] for p in self.plan["parts"]]
        comps = [c["id"] for c in self.plan.get("components", [])]
        return parts, comps

    def _conn(self, owner, conn, where):
        if owner not in self.frames:
            self.signals.append(_signal("%s: unknown owner %r" % (where, owner), where,
                                        "use a part name or component id from the plan"))
            return False
        if conn not in self.frames[owner]:
            self.signals.append(_signal("%s: %r has no connector %r (has: %s)"
                                        % (where, owner, conn, ", ".join(sorted(self.frames[owner]))), where,
                                        "reference one of its connectors, or declare %r on %r" % (conn, owner)))
            return False
        return True

    def _build(self):
        parts, comps = self._owners()
        candidates, broken = [], []   # broken: (parent, child) of edges with a bad reference, already reported
        for c in self.plan.get("components", []):
            where = "plan.components.%s" % c["id"]
            owner, conn = _split(c["attach_to"])
            if owner in comps:
                self.signals.append(_signal("%s: attach_to %r is a component; components attach to parts"
                                            % (where, c["attach_to"]), where, "attach it to a part connector"))
            elif self._conn(owner, conn, where):
                candidates.append(Edge("attach", c["id"], owner, conn, c["id"], self.mounts.get(c["id"])))
            else:
                broken.append((owner, c["id"]))
        for j in self.plan.get("joints", []):
            where = "plan.joints.%s" % j["name"]
            (po, pc), (co, cc) = _split(j["parent"]), _split(j["child"])
            if po == co:
                self.signals.append(_signal("%s: parent and child are both on %r" % (where, po), where,
                                            "a joint connects two different owners"))
                continue
            if self._conn(po, pc, where) and self._conn(co, cc, where):
                candidates.append(Edge("joint", j["name"], po, pc, co, cc, j["type"], j))
            else:
                broken.append((po, co))
        for d in self.plan.get("derived", []):
            self.signals.append(_signal("derived part %r: mirror/pattern parts are not placed by the P1 assembly "
                                        "builder" % d["name"], "plan.derived.%s" % d["name"],
                                        "declare it as a regular part for now"))

        if self.ground not in parts:
            self.signals.append(_signal("ground %r is not a part" % self.ground, "plan.ground",
                                        "set ground to the part fixed to the world"))
        # one parent per owner; the ground has none
        for e in candidates:
            if e.child == self.ground:
                self.signals.append(_signal("%s %r: the ground %r cannot be a child" % (e.kind, e.name, self.ground),
                                            "plan.joints.%s" % e.name, "swap parent and child"))
            elif e.child in self.parent_edge:
                first = self.parent_edge[e.child]
                self.signals.append(_signal(
                    "%r has two parents: %s %r and %s %r (closed loop or duplicate mount)"
                    % (e.child, first.kind, first.name, e.kind, e.name), "plan.joints.%s" % e.name,
                    "keep one parent per part/component; the plan is a tree rooted at %r" % self.ground))
            else:
                self.parent_edge[e.child] = e
        # walk from the ground; anything unreached is detached (or in a loop off the ground)
        children = {}
        for e in self.parent_edge.values():
            children.setdefault(e.parent, []).append(e)
        seen, stack = {self.ground}, [self.ground]
        while stack:
            for e in children.get(stack.pop(), []):
                if e.child not in seen:
                    seen.add(e.child)
                    self.edges.append(e)
                    stack.append(e.child)
        # owners hanging off a broken edge are not reported again as detached
        excused, grew = set(), True
        while grew:
            n = len(excused)
            excused |= {c for p, c in broken if p in seen or p in excused}
            excused |= {e.child for e in self.parent_edge.values() if e.parent in excused}
            grew = len(excused) > n
        for o in parts + comps:
            if o not in seen and o not in excused:
                self.signals.append(_signal("%r is not connected to the ground %r" % (o, self.ground),
                                            "plan", "add a joint or attach_to linking %r into the tree" % o))
        self._check_mimic()

    def _check_mimic(self):
        joints = {j["name"]: j for j in self.plan.get("joints", [])}
        for j in joints.values():
            m = j.get("mimic")
            if m and m["joint"] not in joints:
                self.signals.append(_signal("joint %r mimics unknown joint %r" % (j["name"], m["joint"]),
                                            "plan.joints.%s" % j["name"], "name an existing joint"))
            elif m and joints[m["joint"]].get("mimic"):
                self.signals.append(_signal("joint %r mimics %r, which is itself coupled" % (j["name"], m["joint"]),
                                            "plan.joints.%s" % j["name"], "mimic an independent joint"))

    @property
    def ok(self):
        return not any(s["level"] == "failure" for s in self.signals)

    # ---- poses ------------------------------------------------------------------------------
    def joint_values(self, q=None):
        """Full {joint: value} for a partial pose (missing = 0; coupled joints computed).
        Returns (values, signals): out-of-limit or unknown names are failures."""
        q = dict(q or {})
        joints = {j["name"]: j for j in self.plan.get("joints", [])}
        sigs = [_signal("pose names unknown joint %r" % n, "pose", "joints: %s" % ", ".join(sorted(joints)))
                for n in q if n not in joints]
        values = {}
        for name, j in joints.items():
            m = j.get("mimic")
            if m and m["joint"] in joints:
                if name in q:
                    sigs.append(_signal("pose sets coupled joint %r; it follows %r" % (name, m["joint"]),
                                        "pose", level="warning"))
                v = m["multiplier"] * float(q.get(m["joint"], 0.0)) + m["offset"]
            else:
                v = float(q.get(name, 0.0))
            lim = j.get("limits")
            if lim and j["type"] not in ("continuous",) and not lim["lower"] - 1e-9 <= v <= lim["upper"] + 1e-9:
                sigs.append(_signal("joint %r = %g is outside its limits [%g, %g]" % (name, v, lim["lower"], lim["upper"]),
                                    "pose", "pick q within the plan limits"))
            values[name] = v
        return values, sigs

    def edge_motion(self, e, values):
        return motion(e.jtype, values.get(e.name, 0.0)) if e.kind == "joint" else identity()

    def fk(self, values):
        """{owner: world 4x4} for full joint values (see joint_values). Ground at the world origin."""
        world = {self.ground: identity()}
        for e in self.edges:
            cp = self.frames[e.parent][e.pconn]
            cc = self.frames[e.child][e.cconn] if e.cconn else identity()
            world[e.child] = mul(mul(mul(world[e.parent], cp), self.edge_motion(e, values)), inv(cc))
        return world

    def summary(self):
        return [{"kind": e.kind, "name": e.name, "type": e.jtype, "parent": "%s.%s" % (e.parent, e.pconn),
                 "child": "%s%s" % (e.child, "." + e.cconn if e.cconn else "")} for e in self.edges]
