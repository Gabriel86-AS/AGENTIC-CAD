"""Assembly gates L5 (interference + reach) and L6 (static torque, printed masses), orchestrator side.

    check_assembly(plan, parts, io_dir)
      1. worker op sweep_assembly (validate/sweep.py): built connector frames, owner mass properties,
         OCCT `common` volume of every owner pair at the rest pose + sampled grid (P1 §4.4: 9 per joint,
         at most 25 poses); pairs in one rigid group only at rest; plan.allow_overlap pairs exempt
      2. reach: FK over a dense grid with the built frames (pure Python)
      3. L6: printed part masses (§4.5 model, profile + material), card masses at their model's centre
         of volume (envelope centre without a model), payload at tool; |load| <= stall x derating x n
Signals: L5 interference names the pair and the worst pose; the router (T1.9) decides CODE vs DESIGN.
"""
import time
from dataclasses import dataclass, field
from pathlib import Path

from assembly.builder import card_model
from assembly.kinematics import Tree
from components.card import load_card
from harness.feedback import format_feedback
from harness.run import run_request
from profiles import load_profile
from schemas.plan import AssemblyPlan
from schemas.signal import Signal
from validate.masses import card_com_mm, card_mass_kg, printed_mass_kg
from validate.plan_check import REACH_SAMPLES, payload_mass, reach_sweep, tool_connectors, torque_sweep
from validate.poses import grid

SWEEP_SAMPLES = (9, 25)


@dataclass
class AssemblyCheck:
    ok: bool
    signals: list[Signal]
    collisions: list[dict] = field(default_factory=list)
    reach_mm: float | None = None
    torques: dict = field(default_factory=dict)
    masses: dict = field(default_factory=dict)      # owner -> {mass_kg, com_mm, source}
    owners: dict = field(default_factory=dict)      # owner -> worker mass props (volume, area, com, inertia, bbox)
    frames: dict = field(default_factory=dict)
    mounts: dict = field(default_factory=dict)      # component -> its mount connector
    n_poses: int = 0
    n_checks: int = 0
    elapsed_s: float = 0.0
    feedback: str = ""


def owner_masses(plan: AssemblyPlan, owners: dict, cards: dict, profile: dict) -> dict:
    out = {}
    for p in plan.parts:
        props = owners.get(p.name)
        if props:
            out[p.name] = {"mass_kg": printed_mass_kg(props["volume_mm3"], props["area_mm2"], p.material, profile),
                           "com_mm": props["com_mm"], "source": "printed %s" % p.material}
    for c in plan.components:
        card, props = cards[c.card], owners.get(c.id)
        model = props is not None and card.get("model")
        out[c.id] = {"mass_kg": card_mass_kg(card), "com_mm": props["com_mm"] if model else card_com_mm(card),
                     "source": "card %s" % c.card}
    return out


def _interference_signals(collisions):
    worst = {}
    for c in collisions:
        k = (c["a"], c["b"])
        if k not in worst or c["volume_mm3"] > worst[k]["volume_mm3"]:
            worst[k] = dict(c, n=0)
    for c in collisions:
        worst[(c["a"], c["b"])]["n"] += 1
    out = []
    for (a, b), c in sorted(worst.items(), key=lambda kv: -kv[1]["volume_mm3"]):
        at = "at rest" if c["pose"] == 0 else "at q = %s" % {k: round(v, 3) for k, v in c["q"].items()}
        out.append(Signal(level="failure", gate="L5", location="%s <-> %s" % (a, b),
                          cause="%s and %s overlap by %.1f mm3 %s (%d of the sampled poses)" % (a, b, c["volume_mm3"], at, c["n"]),
                          action="clear the overlap: resize or recess the part that intrudes, narrow the joint limits, "
                                 "or list the pair in plan.allow_overlap if it is intended (press fit, cable)",
                          evidence={"volume_mm3": round(c["volume_mm3"], 3), "pose": c["pose"],
                                    "q": {k: round(v, 4) for k, v in c["q"].items()}, "n_poses": c["n"]}))
    return out


def check_assembly(plan: AssemblyPlan, parts: dict[str, Path], io_dir: Path, timeout_s: float = 300.0) -> AssemblyCheck:
    t0 = time.monotonic()
    cards = {c.card: load_card(c.card) for c in plan.components}
    comps = {c.id: {"card": cards[c.card]["_path"], "fcstd": card_model(c.card)} for c in plan.components}
    pd = plan.model_dump()
    poses = grid(pd, *SWEEP_SAMPLES)
    req = {"op": "sweep_assembly", "plan": pd, "parts": {k: str(Path(v).resolve()) for k, v in parts.items()},
           "components": comps, "poses": poses, "allow_overlap": [list(p) for p in plan.allow_overlap]}
    res = run_request(req, io_dir, timeout_s=timeout_s)
    sigs = [Signal.from_worker(s) for s in res.signals]
    r = res.result if isinstance(res.result, dict) else {}
    out = AssemblyCheck(False, sigs, r.get("collisions", []), owners=r.get("owners") or {}, frames=r.get("frames") or {},
                        mounts=r.get("mounts") or {}, n_poses=r.get("n_poses", 0), n_checks=r.get("n_checks", 0))
    if res.ok and "owners" in r:
        sigs += _interference_signals(out.collisions)
        tree = Tree(pd, out.frames, out.mounts)
        dense = grid(pd, *REACH_SAMPLES)
        tools = tool_connectors(plan)
        tool = tools[0] if len(tools) == 1 else None
        if tool:
            out.reach_mm, q = reach_sweep(tree, tool, dense)
            want = plan.requirements.reach_mm
            if want and out.reach_mm < want - 1e-6:
                sigs.append(Signal(level="failure", gate="L5", location="plan.params",
                                   cause="reach %.1f mm < spec %.1f mm (tool %s)" % (out.reach_mm, want, tool),
                                   action="lengthen the links between the first moving joint and the tool",
                                   evidence={"reach_mm": round(out.reach_mm, 2), "spec_mm": want}))
        profile = load_profile(plan.requirements.print_profile)
        out.masses = owner_masses(plan, out.owners, cards, profile)
        masses = [(o, m["mass_kg"], m["com_mm"]) for o, m in out.masses.items()] + payload_mass(plan, tool)
        out.torques, tsigs = torque_sweep(tree, plan, cards, masses, dense)
        sigs += tsigs
    out.ok = res.ok and not any(s.level == "failure" for s in sigs)
    out.elapsed_s = round(time.monotonic() - t0, 3)
    summary = "%d poses, %d pair checks, %d overlaps" % (out.n_poses, out.n_checks, len(out.collisions))
    if out.reach_mm is not None:
        summary += ", reach %.1f mm" % out.reach_mm
    if out.torques:
        summary += ", torque " + ", ".join("%s %.2f/%.2f N.m" % (j, t["max_abs_Nm"], t["allowed_Nm"]) for j, t in out.torques.items())
    out.feedback = format_feedback("check_assembly", sigs, summary)
    return out
