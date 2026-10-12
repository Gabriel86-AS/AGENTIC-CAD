"""One job end to end (P1 §2): prompt -> spec -> BOM -> plan -> checkpoint -> parts -> gates -> release.

    jobs/<id>/prompt.txt spec.json bom.json plan.json checkpoint.txt parts/*.py context/*.md
              trace.jsonl result.json out/ (pipeline: build, meshes, release, report.json)

The checkpoint comes after the plan validates (it needs the joint tree and the torque estimate) and
before any part is generated (the expensive step). Budgets (plan §4.4): K compiles per part session, one
escalation per part session, `part_rounds` sessions per part, ≤ 2 design rollbacks, `max_rounds` pipeline
runs. A CODE route whose parts have spent their sessions on an assembly / print failure becomes a design
rollback while rollbacks remain (the layout, not the part code, is the problem). On exhaustion the job ends with its open failures listed, never a silent pass.
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from agents import stages
from agents.context import checkpoint_text
from agents.router import route
from agents.trace import Trace
from harness.pipeline import run_job
from schemas.plan import AssemblyPlan

Approve = Callable[[str], "bool | str"]   # True: go; False: abort; str: revise the plan with this note


@dataclass
class Budgets:
    k: int = 5                  # compiles per part session
    part_rounds: int = 3        # part sessions per part (initial + CODE repairs), each may escalate once
    design_rollbacks: int = 2
    checkpoint_revisions: int = 2
    max_rounds: int = 6         # pipeline runs
    workers: int = 3            # parallel part agents


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
    sessions = {p.name: 0 for p in plan.parts}
    robot = slug(job_dir.name)
    last: list = []                                 # open failures of the last pipeline run
    while True:
        if todo:
            exhausted = [n for n in todo if sessions.get(n, 0) >= b.part_rounds]
            if exhausted:
                return finish("fail", "part budget exhausted: %s" % exhausted,
                              [{"part": n, "feedback": todo[n]} for n in exhausted])
            log("-- parts: %s" % ", ".join(todo))
            with ThreadPoolExecutor(max_workers=b.workers) as pool:
                futs = {n: pool.submit(stages.part_agent, plan, n, job_dir, backends, trace, fb, b.k) for n, fb in todo.items()}
                outs = {n: f.result() for n, f in futs.items()}
            for n, o in outs.items():
                sessions[n] = sessions.get(n, 0) + 1
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
        if res.rounds >= b.max_rounds:
            return finish("fail", "pipeline round budget exhausted", last)
        res.rounds += 1
        log("-- gates (round %d)" % res.rounds)
        rep = run_job(job_dir, job_dir / "out", robot=robot, description=description)
        r = route(rep, plan)
        last = [x.model_dump() for x in rep.signals if x.level == "failure"]
        log("   %s -> %s %s" % (rep.failed_stage or "all stages pass", r.kind, ", ".join(r.parts) if r.parts else ""))
        if r.kind == "pass":
            res.package = rep.package
            return finish("pass", "all P1 gates and D1/D2/D4 pass" if description else "all P1 gates pass")
        if r.kind == "stop":
            return finish("fail", r.reason, r.signals)
        if r.kind == "code":
            spent = [n for n in r.parts if sessions.get(n, 0) >= b.part_rounds]
            if not (spent and rep.failed_stage in ("assembly", "print") and res.rollbacks < b.design_rollbacks):
                todo = r.parts
                continue
            # part agents could not clear an assembly/print failure: the layout itself is the problem
            r.signals = [x for v in r.parts.values() for x in v]
            log("   CODE budget spent on %s -> DESIGN" % ", ".join(spent))
        if res.rollbacks >= b.design_rollbacks:
            return finish("fail", "design rollback budget exhausted", r.signals)
        res.rollbacks += 1
        log("-- design rollback %d" % res.rollbacks)
        d = stages.design(spec, bom, backends, trace, job_dir, previous=plan, feedback=r.signals)
        if d.plan is None:
            return finish("fail", "design revision did not pass check_plan", r.signals)
        changed = changed_parts(plan, d.plan)
        plan = d.plan
        for n in list(sessions):
            if plan.part(n) is None:
                sessions.pop(n)
                (job_dir / "parts" / f"{n}.py").unlink(missing_ok=True)
        todo = {n: None for n in changed}
        for n in changed:
            sessions[n] = 0
