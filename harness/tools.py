"""Part-agent harness tools (P1 T1.5, plan §3.4): compile_part and probe_part, orchestrator side.

    compile_part  L0 lint -> worker (SDK build, gates L1, L2, local L3, save .FCStd) -> plan-side L3
                  (connectors within 0.01 mm of their declared pose, labels, bbox within ±20 %)
    probe_part    L0 lint of the snippet -> worker, read-only measurement of the last compiled .FCStd

Both return dataclasses with schemas.Signal lists and a formatted `feedback` string for the agent.
"""
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.feedback import format_feedback
from harness.lint import lint_probe, lint_program
from harness.run import run_request
from schemas.plan import AssemblyPlan, PartSpec
from schemas.signal import Signal

POS_TOL_MM = 0.01
DIR_TOL = 1e-4          # |Δ| of unit axis vectors (~0.006°)
BBOX_TOL = 0.20


@dataclass
class CompileResult:
    ok: bool
    signals: list[Signal]
    report: dict | None = None
    fcstd: str | None = None
    elapsed_s: float = 0.0
    freecad_version: str | None = None
    feedback: str = ""

    def for_agent(self) -> dict:
        """Compact tool output: feedback text plus the facts an agent needs for the next edit."""
        out: dict[str, Any] = {"ok": self.ok, "feedback": self.feedback}
        r = self.report
        if r:
            out.update({"volume_mm3": round(r["volume_mm3"], 3), "bbox": [round(v, 3) for v in r["bound_box"] or []],
                        "connectors": r["connectors"], "params": sorted(r["params"]),
                        "sketches": {n: s["dof"] for n, s in r["sketches"].items()}})
        return out


@dataclass
class ProbeResult:
    ok: bool
    value: Any = None
    signals: list[Signal] = field(default_factory=list)
    feedback: str = ""

    def for_agent(self) -> dict:
        return {"ok": self.ok, "value": self.value} if self.ok else {"ok": False, "feedback": self.feedback}


def _plan_request(plan: AssemblyPlan | None) -> dict:
    if plan is None:
        return {}
    return {"params": {k: p.model_dump() for k, p in plan.params.items()},
            "components": {c.id: c.card for c in plan.components},
            "profile": plan.requirements.print_profile}


def _dist(a, b) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def check_against_plan(report: dict, spec: PartSpec) -> list[Signal]:
    """Plan-side L3: declared connectors exist at their pose with their label; bbox within ±20 %."""
    out = []
    built = report.get("connectors", {})
    for c in spec.connectors:
        decl = "part.connector(%r, origin=%s, z=%s, x=%s, label=%r)" % (c.name, list(c.o), list(c.z), list(c.x), c.label)
        b = built.get(c.name)
        if b is None:
            out.append(Signal(level="failure", gate="L3", cause="plan connector %r is missing" % c.name,
                              location="connector %s" % c.name, action="declare it: %s" % decl))
            continue
        errs = {"o_mm": _dist(b["o"], c.o), "z": _dist(b["z"], c.z), "x": _dist(b["x"], c.x)}
        if errs["o_mm"] > POS_TOL_MM or errs["z"] > DIR_TOL or errs["x"] > DIR_TOL:
            out.append(Signal(
                level="failure", gate="L3", location="connector %s" % c.name,
                cause="connector %r is at o=%s z=%s x=%s; the plan declares o=%s z=%s x=%s" % (
                    c.name, [round(v, 4) for v in b["o"]], [round(v, 4) for v in b["z"]],
                    [round(v, 4) for v in b["x"]], list(c.o), list(c.z), list(c.x)),
                action="place it exactly as declared, origin bound to the plan params: %s" % decl,
                evidence={k: round(v, 6) for k, v in errs.items()}))
        if b["label"] != c.label:
            out.append(Signal(level="failure", gate="L3", location="connector %s" % c.name,
                              cause="connector %r has label %r; the plan says %r" % (c.name, b["label"], c.label),
                              action="use label=%r" % c.label))
    extra = sorted(set(built) - {c.name for c in spec.connectors})
    if extra:
        out.append(Signal(level="note", gate="L3", cause="connectors not in the plan: %s" % extra,
                          action="fine if they are internal frames; joints only use plan connectors"))
    bb = report.get("bound_box")
    if spec.bbox_mm and bb:
        size = [bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2]]
        off = {ax: (s, want) for ax, s, want in zip("xyz", size, spec.bbox_mm)
               if want > 0 and abs(s - want) > BBOX_TOL * want}
        if off:
            out.append(Signal(
                level="failure", gate="L3", location="bounding box",
                cause="size %s mm vs plan intent %s mm (±%d %%): %s off" % (
                    [round(v, 2) for v in size], list(spec.bbox_mm), BBOX_TOL * 100, ", ".join(off)),
                action="check the dimensions driving %s against the plan intent" % ", ".join(off),
                evidence={"size_mm": size, "intent_mm": list(spec.bbox_mm)}))
    material = report.get("material")
    if material and material != spec.material:
        out.append(Signal(level="warning", gate="L3", cause="material %s; the plan says %s" % (material, spec.material),
                          action="Part(%r, material=%r)" % (spec.name, spec.material)))
    return out


def _summary(report: dict) -> str:
    bb = report.get("bound_box")
    size = " x ".join("%.1f" % (bb[i + 3] - bb[i]) for i in range(3)) if bb else "-"
    return "volume %.1f mm3, bbox %s mm, %d connectors, %d sketches, %d params" % (
        report["volume_mm3"], size, len(report["connectors"]), len(report["sketches"]), len(report["params"]))


def compile_part(program: Path, io_dir: Path, out: Path | None = None, part: str | None = None,
                 plan: AssemblyPlan | None = None, timeout_s: float = 60.0) -> CompileResult:
    """Gates L0-L3 for one part program. `part` names the PartSpec in `plan` (default: file stem)."""
    t0 = time.monotonic()
    program = Path(program).resolve()
    part = part or program.stem
    display = "parts/%s" % program.name
    sigs = lint_program(program.read_text(encoding="utf-8"), display)
    if sigs:
        return CompileResult(False, sigs, elapsed_s=round(time.monotonic() - t0, 3),
                             feedback=format_feedback("compile_part", sigs, "stopped at L0, not run"))
    out = Path(out).resolve() if out else Path(io_dir).resolve() / "build" / f"{part}.FCStd"
    out.unlink(missing_ok=True)  # never probe a stale model after a failed compile
    req = {"op": "compile_part", "program": str(program), "display": display, "part": part, "out": str(out),
           **_plan_request(plan)}
    res = run_request(req, io_dir, timeout_s=timeout_s)
    sigs = [Signal.from_worker(s) for s in res.signals]
    report = res.result if isinstance(res.result, dict) else None
    spec = plan.part(part) if plan else None
    if report and spec:
        sigs += check_against_plan(report, spec)
    ok = res.ok and not any(s.level == "failure" for s in sigs)
    return CompileResult(ok, sigs, report, res.artifacts.get("fcstd"), round(time.monotonic() - t0, 3),
                         res.freecad_version,
                         format_feedback("compile_part", sigs, _summary(report) if report else ""))


def probe_part(fcstd: Path, snippet: str, io_dir: Path, timeout_s: float = 30.0) -> ProbeResult:
    sigs = lint_probe(snippet)
    if not sigs:
        res = run_request({"op": "probe_part", "fcstd": str(Path(fcstd).resolve()), "snippet": snippet},
                          io_dir, timeout_s=timeout_s)
        if res.ok:
            return ProbeResult(True, res.result)
        sigs = [Signal.from_worker(s) for s in res.signals]
    return ProbeResult(False, None, sigs, format_feedback("probe_part", sigs))
