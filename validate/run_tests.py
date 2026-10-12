"""Authored assembly tests (P2 §3.2, plan §3.3), orchestrator side: plan.tests -> worker probes -> L5 signals.

The design agent writes the tests as data in plan.json (no code runs on the orchestrator, D31); the helper
names follow Articraft's `run_tests(ctx)` (expect_contact, expect_clearance, allow_overlap) plus expect_reach.

    contact     owners a, b at rest: closest distance <= tol_mm; with `near`, only a's material within
                radius_mm of that connector's origin counts (the worker clips a with a sphere)
    clearance   owners a, b: closest distance >= min_mm over `samples` values of over_joint (others at rest),
                or over the whole L5 sweep without over_joint
    reach       FK reach of a tool connector (default: the plan tool) >= / <= value_mm
    allow_overlap  folded into the interference exemptions (AssemblyPlan.overlap_pairs); reported as passed

Contact and clearance failures are located "a <-> b", so the router sends them to the parts of the pair
(CODE) like an interference; reach failures are located at plan.tests[i] (DESIGN).
"""
from schemas.plan import AssemblyPlan
from schemas.signal import Signal
from validate.plan_check import tool_connectors
from validate.poses import joint_sweep, rest_pose


def probe_requests(plan: AssemblyPlan, poses: list[dict]) -> tuple[list[dict], dict]:
    """(worker probes, {test index: probe index}) for the contact and clearance tests."""
    pd = plan.model_dump()
    probes, index = [], {}
    for i, t in enumerate(plan.tests):
        if t.expect == "contact":
            ps = [rest_pose(pd)]
        elif t.expect == "clearance":
            ps = joint_sweep(pd, t.over_joint, t.samples) if t.over_joint else poses
        else:
            continue
        index[i] = len(probes)
        probes.append({"a": t.a, "b": t.b, "poses": ps})
        if getattr(t, "near", None):
            probes[-1]["near"] = t.near.split(".") + [t.radius_mm]
    return probes, index


def _q(values):
    return {k: round(v, 4) for k, v in (values or {}).items()}


def _fail(i, t, cause, action, location=None, **evidence):
    return Signal(level="failure", gate="L5", location=location or "%s <-> %s" % (t.a, t.b),
                  cause="plan.tests[%d] expect_%s: %s" % (i, t.expect, cause), action=action,
                  evidence={"test": i, **evidence})


def evaluate(plan: AssemblyPlan, probes: list[dict], index: dict, reach_fn) -> tuple[list[dict], list[Signal]]:
    """-> ([{test, expect, ok, ...measured}], signals). reach_fn(tool) -> (reach_mm, q)."""
    results, sigs = [], []
    for i, t in enumerate(plan.tests):
        r = {"test": i, "expect": t.expect, "ok": True}
        if t.expect == "allow_overlap":
            r["reason"] = t.reason
        elif t.expect == "reach":
            tool = t.tool or next(iter(tool_connectors(plan)), None)
            if tool is None:      # a named tool is checked against the plan connectors by check_plan
                r["ok"] = False
                sigs.append(_fail(i, t, "the plan has no tool connector", "name '<owner>.<connector>' as tool",
                                  location="plan.tests[%d]" % i))
            else:
                reach, q = reach_fn(tool)
                r.update(tool=tool, reach_mm=round(reach, 2))
                ok = reach >= t.value_mm - 1e-6 if t.op == ">=" else reach <= t.value_mm + 1e-6
                if not ok:
                    r["ok"] = False
                    sigs.append(_fail(i, t, "reach of %s is %.1f mm, expected %s %.1f mm" % (tool, reach, t.op, t.value_mm),
                                      "lengthen the links between the first moving joint and the tool" if t.op == ">="
                                      else "shorten the links between the first moving joint and the tool",
                                      location="plan.tests[%d]" % i, reach_mm=round(reach, 2), q=_q(q)))
        else:
            p = probes[index[i]]
            if p.get("error") or p.get("min_mm") is None:
                r["ok"] = False
                sigs.append(_fail(i, t, "could not measure: %s" % (p.get("error") or "no pose"),
                                  "name owners that exist in the plan and have a solid"))
                results.append(r)
                continue
            d = p["min_mm"]
            r.update(min_mm=round(d, 3), q=_q(p["q"]))
            if t.expect == "contact":
                if d > t.tol_mm:
                    r["ok"] = False
                    near = " within %g mm of %s" % (t.radius_mm, t.near) if t.near else ""
                    sigs.append(_fail(i, t, "%s and %s are %.2f mm apart at rest%s (tolerance %.2f mm)"
                                      % (t.a, t.b, d, near, t.tol_mm),
                                      "close the gap: move the seat or mating face of the part so it touches the other",
                                      distance_mm=round(d, 3), points=p["points"]))
            elif d < t.min_mm - 1e-6:
                r["ok"] = False
                over = "over %s (%d samples)" % (t.over_joint, t.samples) if t.over_joint else "over the L5 sweep"
                sigs.append(_fail(i, t, "%s and %s come %.2f mm close at q = %s %s (need >= %.2f mm)"
                                  % (t.a, t.b, d, _q(p["q"]), over, t.min_mm),
                                  "recess or shorten the part that approaches, or narrow the joint limits",
                                  distance_mm=round(d, 3), q=_q(p["q"]), points=p["points"]))
        results.append(r)
    return results, sigs
