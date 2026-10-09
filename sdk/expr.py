"""Q: a number that knows the FreeCAD expression it came from.

Every dimension the SDK writes is a Q, so FreeCAD gets an expression (`Params.<alias>` ...) and
the SDK gets the current value for the initial geometry. Plain Python, no FreeCAD import.

FreeCAD quirk (probed 2026-10-09): `Params.width + 3` fails with "Unit mismatch", so literals in
sums carry the unit of the other operand (`Params.width + 3 mm`).
"""
import math
import re

_PARAMS_REF = re.compile(r"\bParams\.")


def fmt(v: float) -> str:
    return "%.12g" % v


class Q:
    """value: float (mm unless unit says otherwise); expr: FreeCAD expression yielding that value.

    `plan` maps plan-level param names this Q depends on to their Param dict, so a Part can add
    them to its Params sheet the first time it sees them.
    """
    __slots__ = ("value", "expr", "unit", "plan")

    def __init__(self, value, expr, unit="mm", plan=None):
        self.value = float(value)
        self.expr = expr
        self.unit = unit
        self.plan = dict(plan or {})

    # ---- rendering ---------------------------------------------------------------------
    def cell(self) -> str:
        """Expression for a cell of the Params sheet itself (aliases without the `Params.` prefix)."""
        return _PARAMS_REF.sub("", self.expr)

    def __repr__(self):
        return "Q(%s = %s %s)" % (self.expr, fmt(self.value), self.unit)

    def __float__(self):
        return self.value

    # ---- arithmetic: + - with Q or a literal (literal gets this unit); * / by a plain number
    def _lit(self, other):
        u = "" if self.unit in ("", "-") else " " + self.unit
        return Q(other, fmt(other) + u, self.unit)

    def _coerce(self, other):
        if isinstance(other, Q):
            if other.unit != self.unit:
                raise TypeError("unit mismatch: %r and %r" % (self, other))
            return other
        if isinstance(other, (int, float)):
            return self._lit(other)
        return NotImplemented

    def _join(self, other, op, value):
        return Q(value, "(%s %s %s)" % (self.expr, op, other.expr), self.unit, {**self.plan, **other.plan})

    def __add__(self, other):
        o = self._coerce(other)
        return NotImplemented if o is NotImplemented else self._join(o, "+", self.value + o.value)

    def __radd__(self, other):
        o = self._coerce(other)
        return NotImplemented if o is NotImplemented else o._join(self, "+", o.value + self.value)

    def __sub__(self, other):
        o = self._coerce(other)
        return NotImplemented if o is NotImplemented else self._join(o, "-", self.value - o.value)

    def __rsub__(self, other):
        o = self._coerce(other)
        return NotImplemented if o is NotImplemented else o._join(self, "-", o.value - self.value)

    def __neg__(self):
        return Q(-self.value, "(-%s)" % self.expr, self.unit, self.plan)

    def __pos__(self):
        return self

    def __mul__(self, k):
        if isinstance(k, Q):
            raise TypeError("Q * Q gives a compound unit; multiply by a plain number")
        if not isinstance(k, (int, float)):
            return NotImplemented
        return Q(self.value * k, "(%s * %s)" % (self.expr, fmt(k)), self.unit, self.plan)

    __rmul__ = __mul__

    def __truediv__(self, k):
        if isinstance(k, Q) or not isinstance(k, (int, float)):
            raise TypeError("divide a Q by a plain number")
        return Q(self.value / k, "(%s / %s)" % (self.expr, fmt(k)), self.unit, self.plan)


def qmax(*qs: Q) -> Q:
    """Largest of several Qs (same unit), as a FreeCAD `max(a; b; ...)` expression."""
    units = {q.unit for q in qs}
    if len(units) != 1:
        raise TypeError("qmax: unit mismatch %r" % (qs,))
    plan = {}
    for q in qs:
        plan.update(q.plan)
    return Q(max(q.value for q in qs), "max(%s)" % "; ".join(q.expr for q in qs), qs[0].unit, plan)


def is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def value(v) -> float:
    return v.value if isinstance(v, Q) else float(v)


class PlanParams:
    """The `P` handed to a part program's build(P): plan-level params as Qs.

    `P.upper_arm_len` -> Q(110, "Params.upper_arm_len"); the Part writes the alias (with min/max)
    into its own sheet when the Q is first used.
    """

    def __init__(self, params: dict | None = None):
        norm = {}
        for name, p in (params or {}).items():
            if is_num(p):
                p = {"value": float(p), "min": float(p), "max": float(p), "unit": "mm"}
            norm[name] = {"value": float(p["value"]), "min": float(p.get("min", p["value"])),
                          "max": float(p.get("max", p["value"])), "unit": p.get("unit", "mm")}
        object.__setattr__(self, "_params", norm)

    def __getattr__(self, name):
        try:
            p = self._params[name]
        except KeyError:
            raise AttributeError("plan has no param %r; plan params: %s" % (name, sorted(self._params))) from None
        unit = "-" if p["unit"] == "-" else p["unit"]
        return Q(p["value"], "Params." + name, unit, {name: p})

    def __setattr__(self, name, v):
        raise AttributeError("plan params are read-only; change the plan instead")

    def names(self):
        return sorted(self._params)
