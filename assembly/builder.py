"""Assembly builder, orchestrator side (P1 T1.6): plan + parts/*.FCStd -> assembly.FCStd.

Deterministic, no LLM. Builds missing card models (components/build.py), then runs the worker op
`build_assembly` (assembly/writer.py): App::Link per part/component, Assembly WB joints, FK preplace,
solve, residual gate. Validators (L5 interference/reach, L6 torque) and the exporter consume the result.
"""
import time
from dataclasses import dataclass, field
from pathlib import Path

from components.build import BUILD, build_card
from components.card import load_card
from harness.feedback import format_feedback
from harness.run import run_request
from schemas.plan import AssemblyPlan
from schemas.signal import Signal


@dataclass
class AssemblyResult:
    ok: bool
    signals: list[Signal]
    fcstd: str | None = None
    tree: list[dict] = field(default_factory=list)
    poses: list[dict] = field(default_factory=list)
    owners: dict = field(default_factory=dict)     # name -> {placement 4x4 (mm), bbox [6] world, saved pose}
    elapsed_s: float = 0.0
    freecad_version: str | None = None
    feedback: str = ""


def card_model(card_id: str, build_dir: Path = BUILD) -> str | None:
    """Path of the card's generated .FCStd, (re)built when missing or older than the card, its generator
    or card.py; None for cards without a generator."""
    card = load_card(card_id)
    if not card.get("model"):
        return None
    path = Path(build_dir) / (card_id.replace("/", "_") + ".FCStd")
    sources = [Path(card["_path"]), Path(card["_path"]).parents[1] / card["model"], Path(__file__).parents[1] / "components" / "card.py"]
    if not path.exists() or path.stat().st_mtime < max(p.stat().st_mtime for p in sources):
        res = build_card(card_id, build_dir)
        if not res.ok or not (res.result or {}).get("pass"):
            raise RuntimeError(f"card model {card_id} failed to build: {[s.cause for s in res.signals]}")
    return str(path.resolve())


def build_assembly(plan: AssemblyPlan, parts: dict[str, Path], io_dir: Path, out: Path,
                   poses: list[dict[str, float]] | None = None, timeout_s: float = 120.0) -> AssemblyResult:
    """`parts`: part name -> compiled .FCStd. `poses`: joint values (rad / mm), poses[0] is saved."""
    t0 = time.monotonic()
    comps = {}
    for c in plan.components:
        comps[c.id] = {"card": load_card(c.card)["_path"], "fcstd": card_model(c.card)}
    req = {"op": "build_assembly", "plan": plan.model_dump(), "parts": {k: str(Path(v).resolve()) for k, v in parts.items()},
           "components": comps, "poses": poses or [{}], "out": str(Path(out).resolve())}
    res = run_request(req, io_dir, timeout_s=timeout_s)
    sigs = [Signal.from_worker(s) for s in res.signals]
    r = res.result if isinstance(res.result, dict) else {}
    ok = res.ok and not any(s.level == "failure" for s in sigs)
    m = res.metrics
    summary = "%d owners, %d joints, residual %.2g mm / %.2g deg" % (
        m.get("n_owners", 0), m.get("n_joints", 0), m.get("max_pos_mm", 0), m.get("max_rot_deg", 0)) if m else ""
    return AssemblyResult(ok, sigs, res.artifacts.get("fcstd"), r.get("tree", []), r.get("poses", []),
                          r.get("owners", {}), round(time.monotonic() - t0, 3), res.freecad_version,
                          format_feedback("build_assembly", sigs, summary))
