"""Sketch helpers: every call adds fully constrained geometry with every dimension bound to Params.

Coordinates are in the sketch's own 2D frame (see Part.sketch for how XY/XZ/YZ map to the part).
A literal 0 coordinate becomes a structural constraint (point on axis / on origin); any other
literal is promoted to a Params alias, so every dimension in the .FCStd stays editable.
After each call the sketch is solved; anything but `solve() == 0 and FullyConstrained` raises
SDKError (gate L2) at build time (D17), so no under-constrained sketch is ever emitted.
"""
import math

import FreeCAD as App
import Part as OCC
import Sketcher

from sdk.errors import SDKError
from sdk.expr import Q, is_num

H_AXIS, V_AXIS, ROOT = -1, -2, -1  # sketch geometry ids: horizontal axis, vertical axis; root point = (-1, 1)


def _is_zero(v) -> bool:
    return is_num(v) and v == 0


def _plus(a, b):
    """a + b where a may be a literal 0 (keeps expressions free of `0 mm + ...`)."""
    return b if _is_zero(a) else a + b


class Sketch:
    def __init__(self, part, obj, name):
        self.part = part
        self.obj = obj
        self.name = name
        self._n = {}

    # ---- internals -------------------------------------------------------------------------
    def _label(self, kind, name):
        if name is not None:
            return name
        k = self._n.get(kind, 0)
        self._n[kind] = k + 1
        return "%s%d" % (kind, k)

    def _len(self, v, label, dim, positive=True):
        q = self.part._q(v, "%s_%s_%s" % (self.name, label, dim))
        if positive and q.value <= 0:
            raise SDKError("sdk", "%s.%s: %s must be > 0, got %g" % (self.name, label, dim, q.value),
                           location=self.name, action="pass a positive length")
        return q

    def _coord(self, v, label, axis):
        """Literal 0 stays 0 (structural constraint); anything else becomes a Q."""
        if _is_zero(v):
            return 0
        return self.part._q(v, "%s_%s_%s" % (self.name, label, axis))

    def _bind(self, cons, cname, q: Q):
        i = self.obj.addConstraint(cons)
        self.obj.renameConstraint(i, cname)
        self.obj.setExpression(".Constraints." + cname, q.expr)
        return i

    def _place(self, g, pos, x, y, label):
        """Constrain point (g, pos) to (x, y); x/y are 0 or Q."""
        sk = self.obj
        if _is_zero(x) and _is_zero(y):
            sk.addConstraint(Sketcher.Constraint("Coincident", g, pos, ROOT, 1))
            return
        if _is_zero(x):
            sk.addConstraint(Sketcher.Constraint("PointOnObject", g, pos, V_AXIS))
        else:
            self._bind(Sketcher.Constraint("DistanceX", ROOT, 1, g, pos, x.value), label + "_x", x)
        if _is_zero(y):
            sk.addConstraint(Sketcher.Constraint("PointOnObject", g, pos, H_AXIS))
        else:
            self._bind(Sketcher.Constraint("DistanceY", ROOT, 1, g, pos, y.value), label + "_y", y)

    def _check(self, call):
        rc = self.obj.solve()
        if rc != 0 or not self.obj.FullyConstrained:
            what = "under-constrained" if rc == 0 and self.obj.DoF > 0 else "over-constrained or conflicting"
            raise SDKError("L2", "sketch %s is %s after %s: solve()=%d DoF=%d"
                           % (self.name, what, call, rc, self.obj.DoF), location=self.name,
                           action="check the %s arguments (degenerate sizes or coincident points)" % call)

    @staticmethod
    def _v(x, y):
        return App.Vector(float(x.value if isinstance(x, Q) else x), float(y.value if isinstance(y, Q) else y), 0)

    # ---- public calls ----------------------------------------------------------------------
    def rect(self, w, h, center=(0, 0), name=None):
        """Axis-aligned rectangle w (sketch x) by h (sketch y) centred at `center`. Dims: <name>_w, <name>_h."""
        label = self._label("rect", name)
        w, h = self._len(w, label, "w"), self._len(h, label, "h")
        cx, cy = self._coord(center[0], label, "cx"), self._coord(center[1], label, "cy")
        sk = self.obj
        x0, y0 = _plus(cx, -(w / 2)), _plus(cy, -(h / 2))
        X0, Y0 = x0.value, y0.value
        pts = [App.Vector(X0, Y0, 0), App.Vector(X0 + w.value, Y0, 0),
               App.Vector(X0 + w.value, Y0 + h.value, 0), App.Vector(X0, Y0 + h.value, 0)]
        g0 = sk.GeometryCount
        for i in range(4):
            sk.addGeometry(OCC.LineSegment(pts[i], pts[(i + 1) % 4]), False)
        sk.addConstraint([Sketcher.Constraint("Coincident", g0 + i, 2, g0 + (i + 1) % 4, 1) for i in range(4)]
                         + [Sketcher.Constraint("Horizontal", g0), Sketcher.Constraint("Horizontal", g0 + 2),
                            Sketcher.Constraint("Vertical", g0 + 1), Sketcher.Constraint("Vertical", g0 + 3)])
        if _is_zero(cx) and _is_zero(cy):
            sk.addConstraint(Sketcher.Constraint("Symmetric", g0, 1, g0 + 2, 1, ROOT, 1))
        else:
            self._place(g0, 1, x0, y0, label + "_corner")
        self._bind(Sketcher.Constraint("DistanceX", g0, 1, g0, 2, w.value), label + "_w", w)
        self._bind(Sketcher.Constraint("DistanceY", g0 + 1, 1, g0 + 1, 2, h.value), label + "_h", h)
        self._check("rect")
        return self

    def circle(self, d, center=(0, 0), name=None):
        """Circle of diameter d. Dims: <name>_d, centre <name>_x/_y."""
        return self.circles(d, [center], name=name)

    def circles(self, d, centers, name=None):
        """Several equal circles (one diameter dimension, the rest Equal). Used by hole patterns."""
        label = self._label("circle", name)
        d = self._len(d, label, "d")
        sk = self.obj
        first = None
        for k, (x, y) in enumerate(centers):
            sub = label if len(centers) == 1 else "%s_%d" % (label, k)
            x, y = self._coord(x, sub, "cx"), self._coord(y, sub, "cy")
            g = sk.addGeometry(OCC.Circle(self._v(x, y), App.Vector(0, 0, 1), d.value / 2), False)
            self._place(g, 3, x, y, sub)
            if first is None:
                first = g
                self._bind(Sketcher.Constraint("Diameter", g, d.value), label + "_d", d)
            else:
                sk.addConstraint(Sketcher.Constraint("Equal", first, g))
        self._check("circle")
        return self

    def slot(self, start, end, width, name=None):
        """Obround from arc centre `start` to arc centre `end`. Dims: <name>_width, centres <name>_start/_end."""
        label = self._label("slot", name)
        width = self._len(width, label, "width")
        sx, sy = self._coord(start[0], label, "sx"), self._coord(start[1], label, "sy")
        ex, ey = self._coord(end[0], label, "ex"), self._coord(end[1], label, "ey")
        S, E = self._v(sx, sy), self._v(ex, ey)
        if (E - S).Length < 1e-9:
            raise SDKError("sdk", "%s.%s: start and end coincide" % (self.name, label), location=self.name,
                           action="use circle() for a round hole")
        r = width.value / 2
        th = math.atan2(E.y - S.y, E.x - S.x)
        n = App.Vector(-math.sin(th), math.cos(th), 0) * r
        Z = App.Vector(0, 0, 1)
        sk = self.obj
        a_s = sk.addGeometry(OCC.ArcOfCircle(OCC.Circle(S, Z, r), th + math.pi / 2, th + 3 * math.pi / 2), False)
        a_e = sk.addGeometry(OCC.ArcOfCircle(OCC.Circle(E, Z, r), th - math.pi / 2, th + math.pi / 2), False)
        l1 = sk.addGeometry(OCC.LineSegment(S - n, E - n), False)
        l2 = sk.addGeometry(OCC.LineSegment(E + n, S + n), False)
        sk.addConstraint([Sketcher.Constraint("Tangent", l1, 1, a_s, 2), Sketcher.Constraint("Tangent", l1, 2, a_e, 1),
                          Sketcher.Constraint("Tangent", l2, 1, a_e, 2), Sketcher.Constraint("Tangent", l2, 2, a_s, 1),
                          Sketcher.Constraint("Equal", a_s, a_e)])
        self._place(a_s, 3, sx, sy, label + "_start")
        self._place(a_e, 3, ex, ey, label + "_end")
        self._bind(Sketcher.Constraint("Diameter", a_s, width.value), label + "_width", width)
        self._check("slot")
        return self

    def polygon(self, n, d, center=(0, 0), name=None):
        """Regular n-gon inscribed in a circle of diameter d, first edge horizontal at the bottom. Dim: <name>_d."""
        if not isinstance(n, int) or n < 3:
            raise SDKError("sdk", "%s: polygon needs an int n >= 3, got %r" % (self.name, n), location=self.name)
        label = self._label("polygon", name)
        d = self._len(d, label, "d")
        cx, cy = self._coord(center[0], label, "cx"), self._coord(center[1], label, "cy")
        C, R = self._v(cx, cy), d.value / 2
        phi0 = -math.pi / 2 - math.pi / n
        pts = [C + App.Vector(R * math.cos(phi0 + 2 * math.pi * k / n), R * math.sin(phi0 + 2 * math.pi * k / n), 0)
               for k in range(n)]
        sk = self.obj
        c = sk.addGeometry(OCC.Circle(C, App.Vector(0, 0, 1), R), True)  # construction circle
        g0 = sk.GeometryCount
        for k in range(n):
            sk.addGeometry(OCC.LineSegment(pts[k], pts[(k + 1) % n]), False)
        cons = [Sketcher.Constraint("Coincident", g0 + k, 2, g0 + (k + 1) % n, 1) for k in range(n)]
        cons += [Sketcher.Constraint("PointOnObject", g0 + k, 1, c) for k in range(n)]
        cons += [Sketcher.Constraint("Equal", g0, g0 + k) for k in range(1, n)]
        cons += [Sketcher.Constraint("Horizontal", g0)]
        sk.addConstraint(cons)
        self._place(c, 3, cx, cy, label)
        self._bind(Sketcher.Constraint("Diameter", c, d.value), label + "_d", d)
        self._check("polygon")
        return self
