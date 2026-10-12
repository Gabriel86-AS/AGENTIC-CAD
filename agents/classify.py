"""Router v2 classifier (P2 §3.5, plan §2.3): Sonnet routes the failures the rule table cannot route for sure.

agents/router.py marks a Route `ambiguous` (with the plan parts a CODE route could name) when a CODE repair
did not change the failure, or an assembly / print failure names plan parts but matches no rule. The
classifier answers through one tool, `submit_route` -> RouteDecision {route: code | design, parts, reason}.
It never routes STOP and never sees passing runs; if it cannot answer, the table's default stands.

    jobs/<id>/routes.jsonl   one line per routing decision (round, source table | classifier | L8, kind, parts, why)
"""
import json
from dataclasses import replace
from pathlib import Path

from pydantic import ValidationError

from agents.review import plan_text
from agents.router import Route, _named
from agents.trace import Trace
from llm.tools import ToolSpec
from schemas.plan import AssemblyPlan
from schemas.signal import RouteDecision

SYSTEM = """You are the router of AgenticCAD, an agentic CAD system for 3D-printable mechanical designs. A plan
(parts with exact connector poses, purchased components mounted on those connectors, joints, parameters)
is built part by part: one part agent writes each part's program against its plan contract, then
deterministic gates check the assembly and its printability. A gate failed and the rule table cannot route
it for sure. Choose who fixes it:

- "code": regenerate the named parts. Their agents see the failures and reshape the part while keeping the
  plan contract (connector poses, joint frames, params, intent). Right when a part's own shape causes the
  failure and the contract leaves room: a wall, rib, recess or pocket, a feature size or position, a fillet,
  a part that is bulkier than it needs to be.
- "design": the design agent revises the plan (link lengths, connector poses, joint limits, where a component
  sits, the part split). Right when the plan's numbers force the failure: no part shape that keeps the
  contract can clear it (two connectors too close for the bodies between them, a limit that swings a part
  through another), or the same failure survived a CODE repair with no sign the part agent can fix it.

Weigh the history and the budgets: a CODE repair that changed nothing is evidence for design; a design
rollback regenerates every part it touches and is the expensive path. Name only plan parts. Call
`submit_route` once, then stop."""


def _params(plan: AssemblyPlan) -> str:
    out = ["`%s` = %g %s [%g..%g]" % (k, p.value, p.unit, p.min, p.max) for k, p in plan.params.items()]
    return "Params: " + (", ".join(out) if out else "(none)")


def _connectors(plan: AssemblyPlan) -> str:
    out = ["Connectors (part frame, mm):"]
    for part in plan.parts:
        out += ["- `%s.%s` (%s): o %s, z %s" % (part.name, c.name, c.label, list(c.o), list(c.z)) for c in part.connectors]
    return "\n".join(out)


def build_prompt(plan: AssemblyPlan, r: Route, failures: list[dict], history: list[dict], budgets: str) -> str:
    hist = "\n".join("- round %d: %s %s (%s)%s" % (h["round"], h["kind"].upper(), ", ".join(h.get("parts") or []),
                                                   h.get("source", "table"), ": " + h["why"] if h.get("why") else "")
                     for h in history) or "(first failure of this job)"
    return ("%s\n%s\n%s\n\n## Failures (failed stage: %s)\n```json\n%s\n```\n\n## Rule table\nDefault: %s %s\n"
            "Not sure because:\n%s\nParts a CODE route could name: %s\n\n## Routing so far\n%s\n\n## Budgets left\n%s\n\n"
            "Route the failures and call submit_route."
            % (plan_text(plan), _params(plan), _connectors(plan), r.reason or "-", json.dumps(failures, indent=1),
               r.kind.upper(), ", ".join(r.parts), "\n".join("- " + a for a in r.ambiguous),
               ", ".join("`%s`" % c for c in r.candidates) or "-", hist, budgets))


def classify(plan: AssemblyPlan, r: Route, failures: list[dict], backend, trace: Trace | None = None,
             history: list[dict] = (), budgets: str = "") -> tuple[Route, str]:
    """-> (route, error). On no valid answer the table route comes back unchanged with the error."""
    names = {p.name for p in plan.parts}
    box: dict = {}

    def submit(args):
        try:
            d = RouteDecision.model_validate(args.get("decision", args))
        except ValidationError as e:
            return {"ok": False, "errors": [".".join(str(x) for x in err["loc"]) + ": " + err["msg"] for err in e.errors()]}
        bad = sorted(set(d.parts) - names)
        if bad:
            return {"ok": False, "errors": ["%s are not plan parts (%s)" % (bad, sorted(names))]}
        box["d"] = d
        return {"ok": True}

    tool = ToolSpec("submit_route", "Submit the routing decision: {route: code | design, parts: [plan parts to "
                                    "regenerate, for code], reason: one sentence}.",
                    {"type": "object", "properties": {
                        "route": {"type": "string", "enum": ["code", "design"]},
                        "parts": {"type": "array", "items": {"type": "string"}},
                        "reason": {"type": "string"}}, "required": ["route", "reason"]}, submit)
    prompt = build_prompt(plan, r, failures, list(history), budgets)
    res = backend.run(SYSTEM, prompt, [tool])
    if trace:
        trace.session("router", res, prompt)
    d = box.get("d")
    if d is None:
        return r, "classifier did not submit a valid decision"
    return to_route(d, failures, r), ""


def to_route(d: RouteDecision, failures: list[dict], table: Route) -> Route:
    """The decision as a Route: each CODE part gets the failures that name it (all failures if none does)."""
    why = "classifier: " + d.reason
    if d.route == "design":
        return replace(table, kind="design", parts={}, signals=failures, reason=why, source="classifier")
    parts = {}
    for p in d.parts:
        mine = [s for s in failures if _named("%s %s" % (s.get("location", ""), s.get("cause", "")), {p})]
        parts[p] = mine or list(failures)
    return replace(table, kind="code", parts=parts, signals=[], reason=why, source="classifier")


def record(job_dir: Path, entry: dict) -> None:
    with (Path(job_dir) / "routes.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
