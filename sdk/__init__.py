"""AgenticCAD SDK v0 (P1 §4.2): a narrow API that emits native, parametric FreeCAD features.

Runs inside freecadcmd (Python 3.11). Part programs import it as `agenticcad` (the only import
gate L0 allows besides `math`):

    from agenticcad import Part

    def build(P):
        part = Part("plate", material="PETG")
        p = part.params(width=P.plate_w, thick=6)
        s = part.sketch("profile", on="XY").rect(p.width, 40)
        part.pad(s, p.thick)
        part.connector("top", origin=(0, 0, p.thick), z=(0, 0, 1), x=(1, 0, 0), label="mount")
        return part

Guarantees: every sketch is fully constrained after each call (else SDKError, gate L2); every
dimension, datum offset, feature length and connector origin is an expression of a Params alias.
"""
import sys

from sdk.errors import SDKError, signal
from sdk.expr import PlanParams, Q
from sdk.part import Datum, Part, configure, context
from sdk.sketch import Sketch

__all__ = ["Part", "Sketch", "Datum", "Q", "PlanParams", "SDKError", "configure", "context", "signal"]

sys.modules.setdefault("agenticcad", sys.modules[__name__])

import packs  # noqa: E402

packs.load_composites()  # pack composites (e.g. part.servo_u_bracket) register on Part; the core names no pack
