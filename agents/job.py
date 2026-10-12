"""One job end to end (P1 §2): prompt -> spec -> BOM -> plan -> checkpoint -> parts -> gates -> release.

    jobs/<id>/prompt.txt spec.json bom.json plan.json checkpoint.txt parts/*.py context/*.md
              trace.jsonl result.json out/ (pipeline: build, meshes, release, report.json)

The checkpoint comes after the plan validates (it needs the joint tree and the torque estimate) and
before any part is generated (the expensive step). After every pipeline run the rule router routes the
failures (agents/router.py); what the table is not sure of goes to the Sonnet classifier (agents/classify.py,
router v2), and the budget table (agents/budget.py) picks the step: part agents, a design rollback, or the
best candidate seen (restored in the job directory) with its open failures, never a silent pass.

L8 (P2 §3.4): once every deterministic gate passes, the reviewer (Opus vision) judges the assembled design. A
major issue is routed once like a gate failure (CODE: the named parts; DESIGN: a rollback) while budgets
remain; the next passing candidate is reviewed again, and a second disagreement is a warning. The reviewed
candidate is the best candidate there is; if the repair ends in any failure, it is restored and the job passes
with the warning: vision never turns a deterministic pass into a failure.
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from agents import classify, review, stages
from agents.budget import PASSED, Budgets, budgets_text, next_step, score
from agents.context import checkpoint_text
from agents.router import _d, route
from agents.trace import Trace
from harness.pipeline import run_job
from schemas.plan import AssemblyPlan

Approve = Callable[[str], "bool | str"]   # True: go; False: abort; str: revise the plan with this note
REVIEW = True     # L8 after the deterministic pipeline passes; tests that are not about L8 switch it off (monkeypatch)
CLASSIFY = True   # router v2 classifier for ambiguous routes; scripted tests without a "router" role switch it off


@dataclass
class JobResult:
    status: str                 # "pass" | "fail" | "aborted"
    job_dir: str
    reason: str = ""
    rounds: int = 0
    rollbacks: int = 0
    parts: dict = field(default_factory=dict)       # part -> {sessions, compiles, escalations, ok}
    open_failures: list = field(default_factory=list)
    totals: dict = field(default_factory=dict)
    elapsed_s: float = 0.0
    package: str | None = None
    review: dict = field(default_factory=dict)     # L8: {status: pass | warning | skipped, rounds, scores, issues, reason}
    best: dict = field(default_factory=dict)       # on exhaustion: {round, failed_stage, restored}
    router: dict = field(default_factory=dict)     # {routes, classified, overruled, errors}


def slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:n].strip("_")
    return s or "job"


def changed_parts(old: AssemblyPlan, new: AssemblyPlan) -> list[str]:
    """Parts to regenerate after a plan revision: new or changed PartSpec, or changed components / joints on them."""
    def facts(plan, name):
        spec = plan.part(name)
        comps = sorted(c.model_dump_json() for c in plan.components if c.attach_to.split(".")[0] == name)
        joints = sorted(j.model_dump_json() for j in plan.joints if name in (j.parent.split(".")[0], j.child.split(".")[0]))
        return spec.model_dump_json() if spec else None, comps, joints
    return [p.name for p in new.parts if old.part(p.name) is None or facts(old, p.name) != facts(new, p.name)]


def _snapshot(job_dir: Path, plan: AssemblyPlan) -> dict:
    return {"plan": plan, "plan_json": (job_dir / "plan.json").read_text(encoding="utf-8"),
            "parts": {p.stem: p.read_text(encoding="utf-8") for p in (job_dir / "parts").glob("*.py")}}


def _restore(job_dir: Path, snap: dict) -> AssemblyPlan:
    (job_dir / "plan.json").write_text(snap["plan_json"], encoding="utf-8")
    for p in (job_dir / "parts").glob("*.py"):
        if p.stem not in snap["parts"]:
            p.unlink()
    for name, text in snap["parts"].items():
        (job_dir / "parts" / f"{name}.py").write_text(text, encoding="utf-8")
    return snap["plan"]


def _verdict_facts(v) -> dict:
    return {"scores": v.scores.model_dump(), "issues": [i.model_dump() for i in v.issues], "route_hint": v.route_hint}


def run(prompt: str, job_dir: Path, backends: stages.Backends, approve: Approve | None = None,
        budgets: Budgets | None = None, description: bool = True, log: Callable[[str], None] = print) -> JobResult:
    t0 = time.monotonic()
    b = budgets or Budgets()
    job_dir = Path(job_dir).resolve()
    (job_dir / "parts").mkdir(parents=True, exist_ok=True)
    (job_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    trace = Trace(job_dir / "trace.jsonl")
    res = JobResult("fail", str(job_dir))

    def finish(status, reason, open_failures=()):
        res.status, res.reason = status, reason
        res.open_failures = list(open_failures)
        res.totals = trace.totals()
        res.elapsed_s = round(time.monotonic() - t0, 1)
        (job_dir / "result.json").write_text(json.dumps(res.__dict__, indent=2, default=str), encoding="utf-8")
        log("== job %s: %s (%s); %d sessions, $%.3f, %.0f s" % (job_dir.name, status, reason, res.totals["sessions"],
                                                               res.totals["cost_usd"], res.elapsed_s))
        return res

    robot = slug(job_dir.name)
    best: dict = {}             # the best candidate seen: {score, round, snap, failed_stage, open, package}

    def keep(rep):
        sc = score(rep)
        if not best or sc >= best["score"]:
            best.update(score=sc, round=res.rounds, snap=_snapshot(job_dir, plan), failed_stage=rep.failed_stage,
                        open=[x.model_dump() for x in rep.signals if x.level == "failure"], package=rep.package)

    def give_up(reason):
        """Budgets exhausted: end with the best candidate seen in the job directory. If that candidate passed every
        gate (only an L8 repair gets here after a pass), the job passes with an L8 warning (D52)."""
        if not best:
            return finish("fail", reason, last)
        restored = best["round"] != res.rounds
        if restored:   # the sources on disk are a later, worse candidate
            log("   restoring the best candidate (round %d)" % best["round"])
            _restore(job_dir, best["snap"])
            again = run_job(job_dir, job_dir / "out", robot=robot, description=description)
            opened = [x.model_dump() for x in again.signals if x.level == "failure"]
            stage, package = again.failed_stage, again.package
        else:
            stage, opened, package = best["failed_stage"], best["open"], best["package"]
        res.best = {"round": best["round"], "failed_stage": stage, "restored": restored}
        if best["score"][0] == PASSED:
            if stage is not None:   # the same sources passed before; only a changed environment gets here
                return finish("fail", "restored L8 candidate no longer passes: %s" % stage, opened)
            res.package = package
            res.review.update(status="warning", reason="the L8 repair failed (%s); kept the reviewed candidate" % reason)
            return finish("pass", "all gates pass; L8 issues open (repair failed)")
        return finish("fail", "%s; best candidate: round %d, failed at %s" % (reason, best["round"], stage), opened)

    log("-- requirements")
    spec, _ = stages.requirements(prompt, backends, trace)
    if spec is None:
        return finish("fail", "requirements agent did not submit a valid spec")
    (job_dir / "spec.json").write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    bom = stages.resolve(spec)
    (job_dir / "bom.json").write_text(json.dumps(bom, indent=2), encoding="utf-8")

    log("-- design")
    d = stages.design(spec, bom, backends, trace, job_dir)
    revisions = 0
    while True:
        if d.plan is None:
            return finish("fail", "design agent did not submit a plan that passes check_plan")
        text = checkpoint_text(d.plan, d.report, bom)
        (job_dir / "checkpoint.txt").write_text(text, encoding="utf-8")
        answer = approve(text) if approve else True
        if answer is True:
            break
        if answer is False or revisions >= b.checkpoint_revisions:
            return finish("aborted", "not approved at the checkpoint")
        revisions += 1
        log("-- design (checkpoint revision %d)" % revisions)
        d = stages.design(spec, bom, backends, trace, job_dir, previous=d.plan,
                          feedback=[{"gate": "checkpoint", "cause": "user: " + str(answer)}])
    plan = d.plan

    todo = {p.name: None for p in plan.parts}       # part -> feedback for its agent
    sessions = {p.name: 0 for p in plan.parts}      # part sessions and escalations per part, under this plan
    escalated = {p.name: 0 for p in plan.parts}
    last: list = []                                 # open failures of the last pipeline run
    prev = None                                     # the route acted on before this run (router v2: repeats)
    routes: list[dict] = []
    res.router = {"routes": 0, "classified": 0, "overruled": 0, "errors": 0}
    l8_repair = False
    while True:
        if todo:
            log("-- parts: %s" % ", ".join(todo))
            with ThreadPoolExecutor(max_workers=b.workers) as pool:
                futs = {n: pool.submit(stages.part_agent, plan, n, job_dir, backends, trace, fb, b.k,
                                       escalated.get(n, 0) < b.escalations) for n, fb in todo.items()}
                outs = {n: f.result() for n, f in futs.items()}
            for n, o in outs.items():
                sessions[n] = sessions.get(n, 0) + 1
                escalated[n] = escalated.get(n, 0) + int(o.escalated)
                st = res.parts.setdefault(n, {"sessions": 0, "compiles": 0, "escalations": 0, "ok": False})
                st["sessions"] += o.sessions
                st["compiles"] += o.compiles
                st["escalations"] += int(o.escalated)
                st["ok"] = o.ok
                if o.look:
                    st["look"] = o.look
                log("   %s: %s after %d compiles%s%s" % (n, "PASS" if o.ok else "FAIL", o.compiles,
                                                     " (escalated)" if o.escalated else "",
                                                     ", L3v %s" % o.look["status"] if o.look else ""))
        res.rounds += 1
        log("-- gates (round %d)" % res.rounds)
        rep = run_job(job_dir, job_dir / "out", robot=robot, description=description)
        keep(rep)
        last = [x.model_dump() for x in rep.signals if x.level == "failure"]
        stage = rep.failed_stage or ""
        r = route(rep, plan, prev)
        if r.kind != "pass" and l8_repair:
            return give_up("%s failed after the L8 repair" % rep.failed_stage)
        if r.ambiguous and CLASSIFY:
            r = _classify(r, rep, plan, backends, trace, routes, res,
                          budgets_text(sessions, escalated, res.rounds, res.rollbacks, b), log)
        log("   %s -> %s %s" % (rep.failed_stage or "all stages pass", r.kind, ", ".join(r.parts) if r.parts else ""))
        if r.kind == "pass":
            res.package = rep.package
            why = "all gates and D1/D2/D4 pass" if description else "all gates pass"
            if not REVIEW or b.review_rounds <= 0:
                return finish("pass", why)
            n = res.review.get("rounds", 0) + 1
            log("-- L8 review (round %d)" % n)
            rv = review.review(plan, rep, job_dir, backends("review"), trace, res.parts, n)
            v = rv.verdict
            review.record(job_dir, {"round": n, **(_verdict_facts(v) if v else {}), "error": rv.error,
                                    "images": [str(p) for p in rv.inputs.images()],
                                    "gifs": [s["gif"] for s in rv.inputs.sweeps]})
            res.review = {"rounds": n, **(_verdict_facts(v) if v else {})}
            if v is None:     # a reviewer that cannot answer does not hold the design back
                res.review.update(status="skipped", reason=rv.error)
                return finish("pass", why + "; L8 skipped")
            log("   L8 %s: geometry %d, detail %d, motion %d, %d issues" % (
                v.route_hint, v.scores.geometry, v.scores.detail, v.scores.motion, len(v.issues)))
            if v.route_hint == "pass":
                res.review["status"] = "pass"
                return finish("pass", why + "; L8 pass")
            r, stage = review.to_route(v), "L8"
            step = next_step(r.kind, r.parts, stage, res.rounds, res.rollbacks, sessions, b)
            blocked = ("the reviewer still disagrees after %d reviews" % n if n >= b.review_rounds else
                       step.reason if step.kind == "best" else "")
            if blocked:
                res.review.update(status="warning", reason=blocked + "; accepted (L8 never blocks)")
                return finish("pass", why + "; L8 issues open")
            l8_repair = True
            log("   L8 -> %s %s" % (r.kind, ", ".join(r.parts)))
        step = next_step(r.kind, r.parts, stage, res.rounds, res.rollbacks, sessions, b)
        res.router["routes"] += 1
        entry = {"round": res.rounds, "stage": stage, "kind": r.kind, "parts": sorted(r.parts), "source": r.source,
                 "why": r.reason, "ambiguous": r.ambiguous, "step": step.kind, "step_reason": step.reason}
        routes.append(entry)
        classify.record(job_dir, entry)
        if step.kind == "stop":
            return finish("fail", r.reason, r.signals)
        if step.kind == "best":
            return give_up(step.reason)
        if step.kind == "code":
            todo, prev = r.parts, r
            continue
        if step.reason:   # D43: part agents could not clear an assembly / print failure: the layout is the problem
            log("   %s -> DESIGN" % step.reason)
        res.rollbacks += 1
        log("-- design rollback %d" % res.rollbacks)
        d = stages.design(spec, bom, backends, trace, job_dir, previous=plan, feedback=r.failures())
        if d.plan is None:
            return give_up("design revision did not pass check_plan")
        changed = changed_parts(plan, d.plan)
        plan = d.plan
        for n in list(sessions):
            if plan.part(n) is None:
                sessions.pop(n)
                escalated.pop(n, None)
                (job_dir / "parts" / f"{n}.py").unlink(missing_ok=True)
        todo, prev = {n: None for n in changed}, None
        for n in changed:
            sessions[n] = escalated[n] = 0


def _classify(r, rep, plan, backends, trace, routes, res, budgets, log):
    """Router v2: Sonnet decides what the table is not sure of; without an answer the table's default stands."""
    fails = [_d(s) for s in rep.signals if s.level == "failure"]
    c, err = classify.classify(plan, r, fails, backends("router"), trace, routes, budgets)
    res.router["classified"] += 1
    if err:
        res.router["errors"] += 1
        log("   ambiguous (%s); %s, keeping %s" % ("; ".join(r.ambiguous), err, r.kind))
        return r
    if c.kind != r.kind or set(c.parts) != set(r.parts):
        res.router["overruled"] += 1
    log("   ambiguous (%s) -> classifier: %s %s" % ("; ".join(r.ambiguous), c.kind, ", ".join(c.parts)))
    return c
