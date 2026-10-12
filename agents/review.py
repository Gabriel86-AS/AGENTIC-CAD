"""L8 assembly review (P2 §3.4, plan §4.1/§4.4): renders + joint keyframes + bbox table + gate reports -> Opus
vision -> ReviewVerdict (scores geometry / detail / motion 1-5, issues, route_hint).

Runs only on a candidate whose deterministic pipeline passed (vision adds issues, never overrides a gate).
agents/job.py drives the rounds: a major issue is routed once (CODE: the named parts; DESIGN: a rollback);
a second disagreement is recorded as a warning and the job passes. A repair that breaks a deterministic gate
is undone (the reviewed candidate is restored), so L8 never turns a pass into a failure.

    jobs/<id>/review/review.json          verdict history (round, scores, issues, route, images)
    jobs/<id>/review/round_<n>/           views/, sweeps/ (<joint>.gif for people, _sheet.png for the model)
"""
import json
import math
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from agents.router import Route
from agents.trace import Trace
from components.card import load_card
from harness.pipeline import JobReport
from llm.backend import RunResult
from llm.tools import ToolSpec
from schemas.plan import AssemblyPlan
from schemas.review import ReviewVerdict
from validate.assembly_views import ReviewInputs, prepare, table_markdown

SYSTEM = """You are the design reviewer (L8) of AgenticCAD. You see an assembled, 3D-printable mechanical design
built from a user's request: printed parts made by part agents from a plan, plus purchased components
(servos, bearings, electronics) placed by the plan. Every deterministic gate already PASSED: sketches fully
constrained, parameter flex, connectors exactly where planned, no interference over the swept joint range,
authored contact / clearance / reach tests, static servo torque, printability (orientation, support, walls,
fits). Do not re-check those numbers; judge what only a picture of the whole design shows.

Images (parallel projection, world frame, z up; corner triad x red, y green, z blue):
1-4. The assembly at rest from iso, iso_back, front, top. Owners are labelled; colours follow the bbox table
     (one colour per link = rigid group; purchased components dark: servos charcoal, bearings grey,
     electronics green). Each moving joint is drawn as RGB axes at its parent connector, labelled; its BLUE
     (z) arrow is the joint axis. The tool frame is labelled too.
5+. One 2 x 2 sheet per moving joint: four keyframes of a sweep over its limits (left to right, top to
     bottom; the joint value is in each caption), the other joints at rest.

Score each 1-5 with a one-line reason:
- geometry: does the whole read as the requested device, in sensible proportions and layout (base stable
  under the load, links where a person would put them, components housed rather than hanging out)?
- detail: are the features a buyer would expect there (servo housed and screwed, horn coupled, bearing
  seats, bay closed or reachable, fastener holes, no crude slabs where a shaped part belongs)?
- motion: does each joint move the right parts about a sensible axis, over a range that serves the task?
5 = would print and ship as is; 4 = minor polish; 3 = works but crude or odd; 2 = a major problem;
1 = does not read as the requested device.

Issues: severity "major" = the design is wrong for the request (missing function, a component left
unsupported or unreachable, a joint that moves the wrong thing or about the wrong axis, a layout that cannot
do the task); "minor" = polish, or a doubt. Hold the build to the plan below: a feature that a part's intent
names (a lug, an idler or pivot, a bearing seat, a bay, a horn hub) and that no image shows IS major, routed
code to that part (its agent can explain it away if the renders mislead); so is a joint the plan supports on
two sides that the images show carried on one side only. Improvements beyond the plan (lighter links, a
lid, feet, extra support the plan did not ask for) are minor. Route each issue:
- "code": one or more parts have the wrong shape but the plan's layout is right; list them in `parts`
  (plan part names only, not components). Their agents keep the connectors and joints and reshape the part.
- "design": the layout itself is wrong (link lengths, joint axes or limits, where a component sits, a
  missing or extra part, a part to split). The design agent revises the plan.
route_hint = "pass" iff no issue is major; "design" if any major issue routes design; otherwise "code".
Name `where` by owner / joint / pair from the table and `what` in one sentence an agent can act on. Report
nothing you cannot point to in the images. Call `submit_verdict` once, then stop."""


@dataclass
class Review:
    verdict: ReviewVerdict | None
    run: RunResult | None
    inputs: ReviewInputs
    error: str = ""


def _deg(v: float) -> str:
    return "%.0f°" % math.degrees(v)


def plan_text(plan: AssemblyPlan) -> str:
    r = plan.requirements
    out = ["## Plan", "Requirements: pack %s, %d DOF%s%s, %s on %s" % (
        r.pack or "none", r.dof, ", reach %g mm" % r.reach_mm if r.reach_mm else "",
        ", payload %g g" % r.payload_g if r.payload_g is not None else "", r.material, r.print_profile)]
    if r.notes:
        out.append("Notes: %s" % r.notes)
    out.append("Printed parts (ground `%s`):" % plan.ground)
    out += ["- `%s`: %s%s" % (p.name, p.intent, " (bbox %s mm)" % list(p.bbox_mm) if p.bbox_mm else "") for p in plan.parts]
    if plan.components:
        out.append("Purchased components:")
        out += ["- `%s`: %s (%s) on `%s`" % (c.id, c.card, load_card(c.card).get("kind", ""), c.attach_to)
                for c in plan.components]
    out.append("Joints:")
    for j in plan.joints:
        lim = ""
        if j.limits:
            lim = " %g..%g mm" % (j.limits.lower, j.limits.upper) if j.type in ("slider", "rack_pinion") else \
                " %s..%s" % (_deg(j.limits.lower), _deg(j.limits.upper))
        out.append("- `%s` %s%s: `%s` -> `%s`%s" % (j.name, j.type, lim, j.parent, j.child,
                                                   ", driven by `%s`" % j.driver if j.driver else ", passive"))
    return "\n".join(out)


def gates_text(rep: JobReport, parts_status: dict | None = None) -> str:
    """The deterministic gate reports, condensed: stage feedback lines, open warnings, L3v outcomes."""
    out = ["## Gate reports (all passed)"]
    for name in ("plan", "assembly", "print", "export", "description"):
        st = rep.stages.get(name)
        if not st:
            continue
        fb = (st.get("feedback") or "").strip().splitlines()
        line = fb[0] if fb else "ok"
        if name == "plan" and st.get("reach_mm") is not None:
            line = "reach %.1f mm (plan estimate)" % st["reach_mm"]
        out.append("- %s: %s" % (name, line))
    warns = [s for s in rep.signals if s.level == "warning"]
    if warns:
        out.append("Warnings:")
        out += ["- [%s] %s: %s" % (s.gate, s.location or "-", s.cause) for s in warns[:12]]
    looks = {p: st["look"] for p, st in (parts_status or {}).items() if st.get("look")}
    if looks:
        out.append("Per-part look check (L3v):")
        for p, lk in sorted(looks.items()):
            issues = "; ".join("%s: %s" % (i["where"], i["what"]) for i in lk.get("issues", []) if i["severity"] == "major")
            out.append("- `%s`: %s%s" % (p, lk["status"], " (" + issues + ")" if issues else ""))
    return "\n".join(out)


def build_prompt(plan: AssemblyPlan, request: str, inputs: ReviewInputs, gates: str) -> str:
    sweeps = "\n".join("- sheet %d: `%s` (%s, %s), keyframes at %s" % (
        5 + i, s["joint"], s["type"], "driven by `%s`" % s["driver"] if s["driver"] else "passive",
        ", ".join("%.1f mm" % q if s["unit"] == "mm" else _deg(q) for q in s["keys"])) for i, s in enumerate(inputs.sweeps))
    return ("## Request\n%s\n\n%s\n\n## Bounding boxes at rest (world frame, mm)\n%s\n\n%s\n\n## Images\n"
            "Views 1-4: iso, iso_back, front, top at rest.\n%s\n\nReview the design and call submit_verdict."
            % (request.strip() or "(no free-text request)", plan_text(plan), table_markdown(inputs.table), gates,
               sweeps or "(no moving joints)"))


def judge(plan: AssemblyPlan, prompt: str, images: list[Path], backend, trace: Trace | None = None) -> tuple[ReviewVerdict | None, RunResult]:
    box: dict = {}
    names = {p.name for p in plan.parts}

    def submit(args):
        try:
            v = ReviewVerdict.model_validate(args.get("verdict", args))
        except ValidationError as e:
            return {"ok": False, "errors": [".".join(str(x) for x in err["loc"]) + ": " + err["msg"] for err in e.errors()]}
        bad = sorted({p for i in v.issues for p in i.parts if p not in names})
        if bad:
            return {"ok": False, "errors": ["parts %s are not plan parts (%s); components and joints go in `where`"
                                            % (bad, sorted(names))]}
        box["verdict"] = v
        return {"ok": True}

    issue = {"type": "object", "properties": {
        "severity": {"type": "string", "enum": ["major", "minor"]}, "where": {"type": "string"},
        "what": {"type": "string"}, "route": {"type": "string", "enum": ["code", "design"]},
        "parts": {"type": "array", "items": {"type": "string"}}}, "required": ["severity", "where", "what", "route"]}
    score = {"type": "integer", "minimum": 1, "maximum": 5}
    tool = ToolSpec("submit_verdict", "Submit the review: {scores: {geometry, detail, motion: 1-5, reasons: {geometry, "
                                      "detail, motion}}, issues: [{severity, where, what, route, parts}], route_hint}.",
                    {"type": "object", "properties": {
                        "scores": {"type": "object", "properties": {
                            "geometry": score, "detail": score, "motion": score,
                            "reasons": {"type": "object", "properties": {k: {"type": "string"} for k in ("geometry", "detail", "motion")}}},
                            "required": ["geometry", "detail", "motion", "reasons"]},
                        "issues": {"type": "array", "items": issue},
                        "route_hint": {"type": "string", "enum": ["pass", "code", "design"]}},
                     "required": ["scores", "issues", "route_hint"]}, submit)
    res = backend.run(SYSTEM, prompt, [tool], images=images)
    if trace:
        trace.session("review", res, prompt)
    return box.get("verdict"), res


def review(plan: AssemblyPlan, rep: JobReport, job_dir: Path, backend, trace: Trace | None = None,
           parts_status: dict | None = None, round_no: int = 1) -> Review:
    job_dir = Path(job_dir)
    inputs = prepare(plan, rep.fcstd, rep.frames, rep.mounts, rep.owners, job_dir / "out" / "io",
                     job_dir / "review" / ("round_%d" % round_no))
    if not inputs.ok:
        return Review(None, None, inputs, "review inputs failed: %s" % "; ".join(s.cause for s in inputs.signals))
    req = job_dir / "prompt.txt"
    prompt = build_prompt(plan, req.read_text(encoding="utf-8") if req.exists() else "", inputs,
                          gates_text(rep, parts_status))
    v, res = judge(plan, prompt, inputs.images(), backend, trace)
    return Review(v, res, inputs, "" if v else "reviewer did not submit a valid verdict")


def to_route(v: ReviewVerdict) -> Route:
    """Major issues -> a router Route (signals gate L8). Minor issues travel along as warnings."""
    def sig(i, level):
        return {"gate": "L8", "level": level, "location": i.where, "cause": i.what,
                "action": "fix it if the design shows it; the reviewer saw renders of the whole assembly"}
    major = [i for i in v.issues if i.severity == "major"]
    minor = [sig(i, "warning") for i in v.issues if i.severity == "minor"]
    if v.route_hint == "pass":
        return Route("pass")
    if v.route_hint == "design":
        return Route("design", signals=[sig(i, "failure") for i in major] + minor, reason="L8 review", source="L8")
    parts: dict = {}
    for i in major:
        for p in i.parts:
            parts.setdefault(p, []).append(sig(i, "failure"))
    for i in v.issues:
        if i.severity == "minor":
            for p in i.parts:
                if p in parts:
                    parts[p].append(sig(i, "warning"))
    return Route("code", parts=parts, reason="L8 review", source="L8")


def record(job_dir: Path, entry: dict) -> dict:
    p = Path(job_dir) / "review" / "review.json"
    h = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"rounds": []}
    h["rounds"].append(entry)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(h, indent=1, default=str), encoding="utf-8")
    return h
