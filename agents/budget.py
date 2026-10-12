"""Budgets and escalation (P2 §3.5, plan §4.4) as one table; agents/job.py asks `next_step` after every routing.

    route    condition (first match)                                              step
    pass     -                                                                     pass (L8 review next)
    stop     -                                                                     stop (pipeline defect)
    any      pipeline runs used = max_rounds                                       best
    code     every routed part has part sessions < part_rounds                      code
    code     a part is spent; failure at assembly / print; rollbacks < max          design (D43: the layout is the problem)
    code     a part is spent otherwise                                              best
    design   rollbacks < design_rollbacks                                           design
    design   otherwise                                                              best

    code   = one part session per routed part: <= k compiles on the part model (Sonnet), then, if it still
             fails and the part has escalations left (1 per part per plan revision), one session on the
             escalation model (Opus). A design revision resets sessions and escalations of the parts it changes.
    design = the design agent revises the plan; changed parts regenerate (keep / regenerate split, ArtiCAD §4.5).
    best   = the job ends "fail" with the best candidate seen restored in the job directory (by `score`) and its
             open failures listed, never a silent pass. A candidate that passed every gate is the best there is:
             an L8 repair that ends anywhere else restores it and the job passes with an L8 warning (D52).
"""
from dataclasses import dataclass, field

STAGE_RANK = {"plan": 0, "part": 1, "assembly": 2, "print": 3, "build": 4, "export": 5, "description": 6}
PASSED = len(STAGE_RANK)


@dataclass
class Budgets:
    k: int = 5                  # compiles per part session
    escalations: int = 1        # escalation sessions per part (per plan revision)
    part_rounds: int = 3        # part sessions per part (initial + CODE repairs)
    design_rollbacks: int = 2
    checkpoint_revisions: int = 2
    max_rounds: int = 6         # pipeline runs
    workers: int = 3            # parallel part agents
    review_rounds: int = 2      # L8 reviews per job, one repair between them
    classify: bool = True       # router v2: Sonnet routes what the rule table is not sure of


@dataclass
class Step:
    kind: str                   # "pass" | "stop" | "code" | "design" | "best"
    reason: str = ""
    parts: list = field(default_factory=list)


def next_step(kind: str, parts, stage: str, rounds: int, rollbacks: int, sessions: dict, b: Budgets) -> Step:
    """kind / parts: the route; stage: where it failed ("part:x", "assembly", "print", "L8" ...)."""
    if kind in ("pass", "stop"):
        return Step(kind)
    if rounds >= b.max_rounds:
        return Step("best", "pipeline round budget exhausted (%d runs)" % b.max_rounds)
    if kind == "code":
        spent = [p for p in parts if sessions.get(p, 0) >= b.part_rounds]
        if not spent:
            return Step("code", parts=list(parts))
        if stage in ("assembly", "print") and rollbacks < b.design_rollbacks:
            return Step("design", "CODE budget spent on %s" % ", ".join(spent))
        return Step("best", "part budget exhausted: %s" % spent)
    if rollbacks < b.design_rollbacks:
        return Step("design")
    return Step("best", "design rollback budget exhausted")


def budgets_text(sessions: dict, escalated: dict, rounds: int, rollbacks: int, b: Budgets) -> str:
    """What is left, for the router classifier's prompt."""
    return "\n".join([
        "- pipeline runs: %d of %d used" % (rounds, b.max_rounds),
        "- design rollbacks: %d of %d used" % (rollbacks, b.design_rollbacks),
        "- part sessions (of %d per part): %s" % (b.part_rounds, ", ".join(
            "`%s` %d%s" % (p, n, " (escalated)" if escalated.get(p) else "") for p, n in sorted(sessions.items())))])


def score(rep) -> tuple:
    """Candidate order: furthest stage reached, then fewer failed stages, then fewer failure signals."""
    if rep.ok:
        return (PASSED, 0, 0)
    failed = [k for k, st in rep.stages.items() if not st.get("ok")]
    return (STAGE_RANK.get((rep.failed_stage or "plan").split(":")[0], 0), -len(failed),
            -sum(1 for s in rep.signals if s.level == "failure"))
