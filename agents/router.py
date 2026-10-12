"""Rule router (P1 §4.4, P2 §3.5, plan §4.4): a failed pipeline stage -> CODE (redo named parts) or DESIGN (revise
the plan), from a fixed table. Where the table only has a default, the Route says why (`ambiguous`) and which
plan parts a CODE route could name (`candidates`); agents/classify.py then asks Sonnet (router v2).

    stage / gate                               route                                        sure?
    plan                                       DESIGN                                       yes
    part:<name> (L0-L4)                        CODE <name>                                  yes
    L4 flex: one plan param fails in >= 2 parts DESIGN (its range conflicts across parts, P2 §3.1)   yes
    assembly L5 overlap / contact / clearance "a <-> b"
        a or b is a plan part                  CODE for the plan parts among a, b           yes
        both are components                    DESIGN (the plan's connectors place them)    yes
    assembly L5 reach (spec or plan.tests), any "plan.*" location; L6 torque   DESIGN       yes
    print L7 build volume                      DESIGN (split into two parts + fixed joint, or shorten)  yes
    print L7 other (manifold, support area, thin wall, fits) on parts/<p>.py   CODE <p>     yes
    any other assembly / print failure         DESIGN                                       no if it names plan parts
    build / export / description               STOP (pipeline defect, not the agents')      yes
    a CODE route whose failure (gate + location) already went CODE to the same part last time   no: the repair did not help

Mixed assembly / print failures go DESIGN (a revised layout regenerates the affected parts anyway).
"""
import re
from dataclasses import dataclass, field

from harness.pipeline import JobReport
from schemas.plan import AssemblyPlan
from schemas.signal import Signal

PART_LOC = re.compile(r"parts/([a-z0-9_]+)\.py")
FLEX_LOC = re.compile(r"parts/([a-z0-9_]+)\.py param ([a-z0-9_]+)")
LINE = re.compile(r":\d+$")


@dataclass
class Route:
    kind: str                                   # "pass" | "code" | "design" | "stop"
    parts: dict = field(default_factory=dict)   # part -> [signal dicts] for CODE
    signals: list[dict] = field(default_factory=list)
    reason: str = ""
    ambiguous: list[str] = field(default_factory=list)   # why the table is not sure (empty: it is)
    candidates: list[str] = field(default_factory=list)  # plan parts a CODE route could name instead
    source: str = "table"                       # "table" | "classifier" | "L8"

    def failures(self) -> list[dict]:
        return self.signals if self.kind != "code" else [x for v in self.parts.values() for x in v]


def _d(s: Signal) -> dict:
    return {k: v for k, v in s.model_dump().items() if v not in ("", {}, None)}


def _key(s: dict) -> tuple:
    return s.get("gate", ""), LINE.sub("", s.get("location", ""))


def _named(text: str, parts: set[str]) -> list[str]:
    return [p for p in sorted(parts) if re.search(r"(?<![a-z0-9_])%s(?![a-z0-9_])" % re.escape(p), text)]


def route(rep: JobReport, plan: AssemblyPlan, previous: Route | None = None) -> Route:
    """previous: the CODE / DESIGN route acted on before this pipeline run (None after a plan revision)."""
    r = _table(rep, plan)
    if r.kind == "code" and previous is not None and previous.kind == "code":
        for p, sigs in sorted(r.parts.items()):
            again = {_key(s) for s in sigs if s.get("level") == "failure"} & \
                    {_key(s) for s in previous.parts.get(p, []) if s.get("level") == "failure"}
            if again:
                r.ambiguous.append("%s: %s failed again after a CODE repair of %s" % (
                    p, ", ".join("%s %s" % k for k in sorted(again)), p))
                r.candidates = sorted(set(r.candidates) | {p})
    return r


def _table(rep: JobReport, plan: AssemblyPlan) -> Route:
    if rep.ok:
        return Route("pass")
    stage = rep.failed_stage or ""
    fails = [s for s in rep.signals if s.level == "failure"]
    parts = {p.name for p in plan.parts}
    if stage == "plan":
        return Route("design", signals=[_d(s) for s in fails], reason="plan validator")
    flexed: dict = {}
    for s in fails:
        m = FLEX_LOC.match(s.location) if s.gate == "L4" else None
        if m and m.group(2) in plan.params:
            flexed.setdefault(m.group(2), set()).add(m.group(1))
    conflict = sorted(p for p, ps in flexed.items() if len(ps) >= 2)
    if conflict:
        return Route("design", signals=[_d(s) for s in fails if s.gate == "L4"],
                     reason="plan param range conflicts across parts (L4): %s" % ", ".join(conflict))
    if stage.startswith("part:"):
        name = stage.split(":", 1)[1]
        mine = [_d(s) for s in fails if f"parts/{name}.py" in s.location or s.location.startswith("connector")
                or s.gate in ("L0", "L1", "L2", "L3", "L4", "sdk")]
        return Route("code", parts={name: mine or [_d(s) for s in fails]}, reason="part gates")
    if stage in ("assembly", "print"):
        code: dict = {}
        design: list[dict] = []
        unsure: list[str] = []
        cands: set[str] = set()
        for s in fails:
            if stage == "assembly" and s.gate == "L5" and "<->" in s.location:
                owners = [o.strip() for o in s.location.split("<->")]
                hit = [o for o in owners if o in parts]
                if hit:
                    for o in hit:
                        code.setdefault(o, []).append(_d(s))
                    continue
            elif stage == "print" and s.gate == "L7" and "build volume" not in s.cause:
                m = PART_LOC.search(s.location)
                if m and m.group(1) in parts:
                    code.setdefault(m.group(1), []).append(_d(s))
                    continue
            if not (s.gate == "L6" or s.location.startswith("plan") or "build volume" in s.cause or "<->" in s.location):
                named = _named("%s %s" % (s.location, s.cause), parts)
                if named:
                    unsure.append("%s [%s] names parts %s but no table rule" % (s.location or "-", s.gate, named))
                    cands |= set(named)
            design.append(_d(s))
        if design:
            return Route("design", signals=design + [x for v in code.values() for x in v], reason=stage,
                         ambiguous=unsure, candidates=sorted(cands | set(code)) if unsure else [])
        return Route("code", parts=code, reason=stage)
    return Route("stop", signals=[_d(s) for s in fails], reason="%s failed: pipeline defect" % stage)
