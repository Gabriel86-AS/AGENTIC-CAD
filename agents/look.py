"""L3v part look check (P2 §3.4, plan §4.1): 4 renders + the part's contract -> vision model -> LookVerdict.

Runs on a candidate that already passes L0-L4 (deterministic gates first; vision adds issues, never
overrides a pass/fail). The judge answers a fixed checklist through one tool, `submit_verdict`.
stages.part_agent drives the rounds: a major issue goes back to the part agent once; a second
disagreement is recorded as a warning and the part is accepted (L3v never blocks on its own).

    jobs/<id>/look/<part>.json   verdict history + the source hash it judged (an unchanged program that
                                 already matched is not judged again)
    jobs/<id>/renders/<part>/look/<part>_<view>.png
"""
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from agents.context import part_excerpt
from agents.trace import Trace
from llm.backend import RunResult
from llm.tools import ToolSpec
from schemas.plan import AssemblyPlan
from schemas.review import LookVerdict

SYSTEM = """You are the look checker (L3v) of AgenticCAD. You see four renders of ONE 3D-printable part, built
by a part agent from the contract below. Deterministic gates already passed: the program compiles, sketches
are fully constrained, the solid is valid, connectors sit exactly at their declared poses, the bounding box is
within 20 % of the intent, walls and fits print. Do not re-check numbers; judge what only a picture shows.

Renders: parallel projection, part frame (corner triad x red, y green, z blue). Each connector is drawn as
RGB axes (x red, y green, z blue) labelled at its z tip. Purchased components (servos, bearings, boards) and
neighbouring parts are NOT drawn: only this part.

Checklist:
1. Overall form: does the shape read as what the intent describes (plate, bracket, U-bracket, link, box ...)?
2. Features the intent names: present and in the described place (servo pocket or cut-out, horn recess and
   screw holes, bearing seat, bay, cable channel, ribs, split tongue / slot ...)?
3. Connectors: on the face or edge the intent implies (a horn seat on the face the horn meets, the tool at
   the link tip), z pointing out of or along that face as the joint needs?
4. Anything obviously wrong: floating or detached bits, a cut that removes a face the intent needs, a
   feature on the wrong side, a mirrored layout.

Severity: "major" = the part is wrong for its intent (missing or misplaced feature, wrong face, wrong form);
a feature the intent names that no view shows IS major (the part agent can explain it away if the render
misleads); "minor" = cosmetic, or a doubt about something you can see. Name `where` by feature, face or connector and `what` in one sentence
the part agent can act on. matches_intent = false iff at least one issue is major. Report no issue you cannot
point to in the images. Call `submit_verdict` once, then stop."""


@dataclass
class Look:
    verdict: LookVerdict | None
    run: RunResult | None
    renders: list[Path]
    error: str = ""


def source_hash(program: Path) -> str:
    return hashlib.sha256(Path(program).read_bytes()).hexdigest()[:16]


def connector_list(report: dict) -> str:
    return "\n".join("- `%s` (%s): o %s, z %s, x %s" % (n, c.get("label"), [round(v, 2) for v in c["o"]],
                                                       [round(v, 3) for v in c["z"]], [round(v, 3) for v in c["x"]])
                     for n, c in sorted((report.get("connectors") or {}).items()))


def judge(plan: AssemblyPlan, name: str, renders: list[Path], report: dict, backend, trace: Trace | None = None) -> Look:
    box: dict = {}

    def submit(args):
        try:
            v = LookVerdict.model_validate(args.get("verdict", args))
        except ValidationError as e:
            return {"ok": False, "errors": [str(err["loc"]) + ": " + err["msg"] for err in e.errors()]}
        if v.matches_intent == any(i.severity == "major" for i in v.issues):
            return {"ok": False, "errors": ["matches_intent must be false iff some issue is major"]}
        box["verdict"] = v
        return {"ok": True}

    tool = ToolSpec("submit_verdict", "Submit the look verdict: {matches_intent, issues: [{severity: major|minor, "
                                      "where, what}]}.",
                    {"type": "object", "properties": {"matches_intent": {"type": "boolean"}, "issues": {
                        "type": "array", "items": {"type": "object", "properties": {
                            "severity": {"type": "string", "enum": ["major", "minor"]}, "where": {"type": "string"},
                            "what": {"type": "string"}}, "required": ["severity", "where", "what"]}}},
                     "required": ["matches_intent", "issues"]}, submit)
    bb = report.get("bound_box") or []
    size = " x ".join("%.1f" % (bb[i + 3] - bb[i]) for i in range(3)) if bb else "-"
    prompt = ("%s\n## Built model\nbbox %s mm; connectors as built:\n%s\n\nViews attached in order: %s. "
              "Judge the part against its contract and call submit_verdict."
              % (part_excerpt(plan, name), size, connector_list(report), ", ".join(p.stem.split(name + "_", 1)[-1] for p in renders)))
    res = backend.run(SYSTEM, prompt, [tool], images=renders)
    if trace:
        trace.session("look:%s" % name, res, prompt)
    v = box.get("verdict")
    return Look(v, res, renders, "" if v else "judge did not submit a valid verdict")


def feedback(v: LookVerdict) -> list[dict]:
    """Issues for the part agent's repair prompt (router-style signal dicts)."""
    return [{"gate": "L3v", "level": "failure" if i.severity == "major" else "warning", "location": i.where,
             "cause": i.what, "action": "fix it if the render shows it; if the render misleads, say why in your summary"}
            for i in v.issues]


def history(job_dir: Path, name: str) -> dict:
    p = Path(job_dir) / "look" / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"part": name, "rounds": []}


def record(job_dir: Path, name: str, entry: dict) -> dict:
    h = history(job_dir, name)
    h["rounds"].append(entry)
    p = Path(job_dir) / "look" / f"{name}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(h, indent=1), encoding="utf-8")
    return h
