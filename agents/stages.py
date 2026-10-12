"""Agent stages of one job (P1 §2): requirements -> resolver -> design -> part agents.

Each LLM stage is one backend session whose only effects go through our tools: submit_spec / check_plan /
submit_plan validate before anything is stored; part agents write only parts/<own>.py (PartWorkspace).
`Backends` maps a role to a backend (model routing, plan §2.3); tests inject scripted backends.
"""
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from agents import look, prompts
from agents.context import part_excerpt
from agents.trace import Trace
from components.card import card_ids, load_card
from harness import tools as harness_tools
from llm.backend import LLMBackend, RunResult
from llm.tools import PartWorkspace, ToolDenied, ToolSpec
from schemas.plan import AssemblyPlan, Spec
from schemas.signal import Signal
from validate.plan_check import PlanReport, check_plan

ROOT = Path(__file__).resolve().parents[1]
Backends = Callable[[str], LLMBackend]   # role: "requirements" | "design" | "part" | "part_escalated" | "look" | "review"
DEFAULT_GENERIC_BAY = "electronics/generic_bay"
DEFAULT_SERVO = "servo/mg996r"
_COMPILE_LOG = threading.Lock()   # parallel part agents append to one compiles.jsonl


def _errors(e: ValidationError) -> list[str]:
    return ["%s: %s" % (".".join(str(x) for x in err["loc"]) or "root", err["msg"]) for err in e.errors()]


def _sigs(sigs: list[Signal], levels=("failure", "warning")) -> list[dict]:
    return [{k: v for k, v in s.model_dump().items() if v not in ("", {}, None)} for s in sigs if s.level in levels]


# ---- requirements ------------------------------------------------------------------------------
def requirements(prompt: str, backends: Backends, trace: Trace) -> tuple[Spec | None, RunResult]:
    box: dict = {}

    def submit(args):
        try:
            spec = Spec.model_validate(args.get("spec", args))
        except ValidationError as e:
            return {"ok": False, "errors": _errors(e)}
        unknown = [c for c in spec.components_requested + ([] if spec.electronics == "generic" else spec.electronics)
                   if c not in card_ids()]
        if unknown:
            return {"ok": False, "errors": ["no card %r in the library" % c for c in unknown]}
        box["spec"] = spec
        return {"ok": True}

    tool = ToolSpec("submit_spec", "Submit the Spec (validated against the schema and the card library).",
                    {"type": "object", "properties": {"spec": {"type": "object"}}, "required": ["spec"]}, submit)
    backend = backends("requirements")
    res = backend.run(prompts.requirements_system(), prompt, [tool])
    trace.session("requirements", res, prompt)
    return box.get("spec"), res


# ---- resolver (deterministic in P1: the library is small) ---------------------------------------
def resolve(spec: Spec) -> dict:
    """Cards the design may use: requested ones, a servo default for actuated packs, electronics."""
    cards = list(dict.fromkeys(spec.components_requested))
    actuated = spec.pack == "manipulator" or any(load_card(c).get("kind", "").startswith("actuator") for c in cards)
    if spec.pack == "manipulator" and not any(load_card(c).get("kind", "").startswith("actuator") for c in cards):
        cards.append(DEFAULT_SERVO)
    if spec.electronics == "generic":
        if actuated:
            cards.append(DEFAULT_GENERIC_BAY)
    else:
        cards += [c for c in spec.electronics if c not in cards]
    items = []
    for c in cards:
        card = load_card(c)
        items.append({"card": c, "kind": card.get("kind"), "verified": bool(card.get("verified")),
                      "mass_kg": card.get("mass_kg")})
    return {"items": items, "cards": cards}


# ---- design ------------------------------------------------------------------------------------
@dataclass
class DesignOutcome:
    plan: AssemblyPlan | None
    report: PlanReport | None
    run: RunResult
    checks: int = 0


def design(spec: Spec, bom: dict, backends: Backends, trace: Trace, job_dir: Path,
           feedback: list[dict] | None = None, previous: AssemblyPlan | None = None, max_checks: int = 8) -> DesignOutcome:
    box: dict = {"checks": 0}

    def parse(args):
        raw = args.get("plan", args)
        if isinstance(raw, str):
            raw = json.loads(raw)
        raw = dict(raw)
        raw["requirements"] = spec.model_dump()   # the Spec is fixed; the plan cannot drift from it
        return AssemblyPlan.model_validate(raw)

    def check(args, submit=False):
        if box["checks"] >= max_checks:
            raise ToolDenied("check budget of %d used up; submit your best plan or stop" % max_checks)
        box["checks"] += 1
        try:
            plan = parse(args)
        except (ValidationError, json.JSONDecodeError) as e:
            return {"ok": False, "errors": _errors(e) if isinstance(e, ValidationError) else [str(e)]}
        unknown = [c.card for c in plan.components if c.card not in bom["cards"]]
        if unknown:
            return {"ok": False, "errors": ["card %r is not in the bill of materials %s" % (c, bom["cards"]) for c in unknown]}
        rep = check_plan(plan)
        out = {"ok": rep.ok, "signals": _sigs(rep.signals), "dof": rep.dof, "tool": rep.tool,
               "reach_mm": rep.reach_mm, "torques": rep.torques}
        if submit and rep.ok:
            box["plan"], box["report"] = plan, rep
            (job_dir / "plan.json").write_text(json.dumps(plan.model_dump(), indent=2), encoding="utf-8")
            out["submitted"] = True
        elif submit:
            out["submitted"] = False
        return out

    schema = {"type": "object", "properties": {"plan": {"type": "object"}}, "required": ["plan"]}
    tools = [
        ToolSpec("check_plan", "Validate a plan: schema, card refs, kinematic tree, DOF, servo ranges, tool, reach, "
                               "plan-level static torque. Returns signals (cause | location | action) and estimates.",
                 schema, check),
        ToolSpec("submit_plan", "Submit the final plan; accepted only if it passes check_plan. Writes plan.json.",
                 schema, lambda a: check(a, submit=True)),
        ToolSpec("read_file", "Read a file under components/, packs/, docs/ or bench/golden/.",
                 {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}, _reader(
                     [ROOT / "components", ROOT / "packs", ROOT / "docs", ROOT / "bench" / "golden"])),
    ]
    prompt = "Spec:\n```json\n%s\n```\nBill of materials (cards you may use): %s\n" % (
        json.dumps(spec.model_dump(), indent=1), bom["cards"])
    if previous is not None:
        prompt += ("\nYour previous plan failed downstream gates. Revise it; keep what works.\nPrevious plan:\n```json\n%s\n```\n"
                   % json.dumps(previous.model_dump(), indent=1))
    if feedback:
        prompt += "\nSignals to fix:\n```json\n%s\n```\n" % json.dumps(feedback, indent=1)
    prompt += "\nWrite the plan, check it, submit it."
    res = backends("design").run(prompts.design_system(spec.pack, bom["cards"]), prompt, tools)
    trace.session("design", res, prompt)
    return DesignOutcome(box.get("plan"), box.get("report"), res, box["checks"])


def _reader(roots: list[Path]):
    roots = [r.resolve() for r in roots]

    def read(args):
        p = Path(args["path"])
        p = (p if p.is_absolute() else ROOT / p).resolve()
        if not any(p.is_relative_to(r) for r in roots) or not p.is_file():
            raise ToolDenied("%s is not a readable file (allowed: components/, packs/, docs/, bench/golden/)" % args["path"])
        return {"path": str(p.relative_to(ROOT)), "content": p.read_text(encoding="utf-8")}

    return read


# ---- part agents -------------------------------------------------------------------------------
LOOK = True   # L3v after a passing compile; tests that are not about L3v switch it off (monkeypatch)


@dataclass
class PartOutcome:
    part: str
    ok: bool
    sessions: int = 0
    compiles: int = 0
    escalated: bool = False
    feedback: str = ""
    runs: list[RunResult] = field(default_factory=list)
    look: dict = field(default_factory=dict)   # L3v: {status: pass | warning | skipped, rounds, issues, reason}


def part_agent(plan: AssemblyPlan, name: str, job_dir: Path, backends: Backends, trace: Trace,
               feedback: list[dict] | None = None, k: int = 5, escalate: bool = True, look_rounds: int = 2) -> PartOutcome:
    """≤ K compiles with the part model; if it still fails, one more session on the escalation model.
    A passing part then goes through L3v: ≤ look_rounds judge calls with one repair session between them."""
    ctx = job_dir / "context"
    ctx.mkdir(parents=True, exist_ok=True)
    excerpt = part_excerpt(plan, name)
    (ctx / f"{name}.md").write_text(excerpt, encoding="utf-8")
    out = PartOutcome(name, False)

    def session(role: str, extra: str):
        ws = PartWorkspace(job_dir, name, read_roots=[ctx, ROOT / "docs", ROOT / "components", ROOT / "bench" / "golden"],
                           plan=plan, max_compiles=k)
        prompt = prompts.PART_PROMPT.format(name=name)
        if ws.own.exists():
            prompt += "\nparts/%s.py already exists from an earlier attempt; read it and repair it." % name
        prompt += extra
        res = backends(role).run(prompts.part_system(name, excerpt, k), prompt, ws.tools())
        trace.session("part:%s%s" % (name, ":escalated" if role != "part" else ""), res, prompt)
        out.sessions += 1
        out.compiles += ws.compiles
        out.runs.append(res)
        with _COMPILE_LOG, (job_dir / "compiles.jsonl").open("a", encoding="utf-8") as f:   # bench metrics (DoF-0 rate)
            for e in ws.log:
                if e["tool"] == "compile_part" and e["outcome"] != "denied":
                    f.write(json.dumps({"part": name, "role": role, "ok": e["outcome"] == "ok", "gates": e["gates"],
                                        "n_sketches": e["n_sketches"], "n_dof0": e["n_dof0"]}) + "\n")
        final = harness_tools.compile_part(ws.own, ws.io, out=ws.fcstd, part=name, plan=plan) if ws.own.exists() else None
        out.ok = bool(final and final.ok)
        out.feedback = final.feedback if final else "parts/%s.py was never written" % name
        return ws, final

    roles = ["part"] + (["part_escalated"] if escalate else [])
    for role in roles:
        extra = ""
        if feedback:
            extra += "\nThe assembled design failed these checks involving your part; fix them:\n```json\n%s\n```" % (
                json.dumps(feedback, indent=1))
        if out.feedback and role == "part_escalated":
            extra += "\nThe previous attempt ended with:\n%s" % out.feedback
        ws, final = session(role, extra)
        out.escalated = role != "part"
        if out.ok:
            break
    if out.ok and LOOK and look_rounds > 0:
        _look_rounds(plan, name, job_dir, backends, trace, ws, final, session, out, look_rounds)
    return out


def _look_rounds(plan, name, job_dir, backends, trace, ws, final, session, out: PartOutcome, rounds: int):
    """L3v on an accepted candidate. Never turns a deterministic pass into a failure: a repair that breaks the
    compile is rolled back to the passing program, and a second disagreement is a warning."""
    from validate.render import render_part   # PyVista import is slow; only L3v pays it
    h = look.history(job_dir, name)
    last = h["rounds"][-1] if h["rounds"] else None
    if last and last["source"] == look.source_hash(ws.own) and last.get("matches_intent"):
        out.look = {"status": "pass", "rounds": 0, "cached": True}
        return
    for i in range(rounds):
        src = look.source_hash(ws.own)
        pngs, _ = render_part(ws.fcstd, final.report["connectors"], ws.io, job_dir / "renders" / name / "look", name=name)
        if not pngs:
            out.look = {"status": "skipped", "rounds": i, "reason": "render failed"}
            return
        lk = look.judge(plan, name, list(pngs.values()), final.report, backends("look"), trace)
        v = lk.verdict
        look.record(job_dir, name, {"source": src, "matches_intent": v.matches_intent if v else None,
                                    "issues": [x.model_dump() for x in v.issues] if v else [], "error": lk.error,
                                    "renders": [str(p) for p in pngs.values()]})
        if v is None:     # a judge that cannot answer does not hold the part back
            out.look = {"status": "skipped", "rounds": i + 1, "reason": lk.error}
            return
        issues = [x.model_dump() for x in v.issues]
        if v.matches_intent:
            out.look = {"status": "pass", "rounds": i + 1, "issues": issues}
            return
        if i == rounds - 1:
            out.look = {"status": "warning", "rounds": i + 1, "issues": issues,
                        "reason": "look check still disagrees after %d rounds; accepted (L3v never blocks)" % rounds}
            return
        good = ws.own.read_text(encoding="utf-8")
        ws, again = session("part", "\nYour part compiles, but the look check (L3v: a vision model looking at renders "
                                    "of it) reports these issues against your contract:\n```json\n%s\n```\nFix the "
                                    "real ones and keep compile_part passing." % json.dumps(look.feedback(v), indent=1))
        if again is None or not again.ok:
            ws.own.write_text(good, encoding="utf-8")
            final = harness_tools.compile_part(ws.own, ws.io, out=ws.fcstd, part=name, plan=plan)
            out.ok, out.feedback = final.ok, final.feedback
            out.look = {"status": "warning", "rounds": i + 1, "issues": issues,
                        "reason": "the L3v repair broke the compile; kept the passing program"}
            return
        final = again
