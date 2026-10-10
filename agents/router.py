"""Rule router (P1 §4.4, plan §4.4): a failed pipeline stage -> CODE (redo named parts) or DESIGN (revise
the plan), from a fixed table. P2 wraps this table with an LLM classifier for ambiguous cases.

    stage / gate                               route
    plan                                       DESIGN
    part:<name> (L0-L3)                        CODE <name>
    assembly L5 overlap "a <-> b"              CODE for the plan parts among a, b (DESIGN if neither is a part)
    assembly L5 reach, tree; L6 torque         DESIGN
    print L7 build volume                      DESIGN (split or shorten the part)
    print L7 other (watertight, thin wall)     CODE <part>
    build / export / description               STOP (pipeline defect, not the agents')
"""
import re
from dataclasses import dataclass, field

from harness.pipeline import JobReport
from schemas.plan import AssemblyPlan
from schemas.signal import Signal

PART_LOC = re.compile(r"parts/([a-z0-9_]+)\.py")


@dataclass
class Route:
    kind: str                                   # "pass" | "code" | "design" | "stop"
    parts: dict = field(default_factory=dict)   # part -> [signal dicts] for CODE
    signals: list[dict] = field(default_factory=list)
    reason: str = ""


def _d(s: Signal) -> dict:
    return {k: v for k, v in s.model_dump().items() if v not in ("", {}, None)}


def route(rep: JobReport, plan: AssemblyPlan) -> Route:
    if rep.ok:
        return Route("pass")
    stage = rep.failed_stage or ""
    fails = [s for s in rep.signals if s.level == "failure"]
    parts = {p.name for p in plan.parts}
    if stage == "plan":
        return Route("design", signals=[_d(s) for s in fails], reason="plan validator")
    if stage.startswith("part:"):
        name = stage.split(":", 1)[1]
        mine = [_d(s) for s in fails if f"parts/{name}.py" in s.location or s.location.startswith("connector")
                or s.gate in ("L0", "L1", "L2", "L3", "sdk")]
        return Route("code", parts={name: mine or [_d(s) for s in fails]}, reason="part gates")
    if stage in ("assembly", "print"):
        code: dict = {}
        design: list[dict] = []
        for s in fails:
            if stage == "assembly" and s.gate == "L5" and "<->" in s.location:
                owners = [o.strip() for o in s.location.split("<->")]
                hit = [o for o in owners if o in parts]
                if hit:
                    for o in hit:
                        code.setdefault(o, []).append(_d(s))
                    continue
            if stage == "print" and s.gate == "L7" and "build volume" not in s.cause:
                m = PART_LOC.search(s.location)
                if m and m.group(1) in parts:
                    code.setdefault(m.group(1), []).append(_d(s))
                    continue
            design.append(_d(s))
        if design:
            return Route("design", signals=design + [x for v in code.values() for x in v], reason=stage)
        return Route("code", parts=code, reason=stage)
    return Route("stop", signals=[_d(s) for s in fails], reason="%s failed: pipeline defect" % stage)
