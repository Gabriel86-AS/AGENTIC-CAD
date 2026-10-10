"""Deterministic job pipeline (P1 §2, no LLM): a job directory with plan.json + parts/<part>.py through every
P1 gate and the export, in order. The golden solutions (T1.12) run it as is; the agents (T1.9) call the same
stages between their turns.

    plan    check_plan (schema, tree, DOF, reach, L6-plan)       DESIGN
    parts   compile_part per plan part (L0-L3)                    CODE
    L5/L6   check_assembly (interference sweep, reach, torque)    CODE / DESIGN
    L7      check_print (build volume, watertight, min wall)      CODE / DESIGN
    build   build_assembly -> assembly.FCStd (rest pose)
    export  export_package (URDF + meshes + print STLs)
    D1-D4   check_description (check_urdf, Pinocchio, gz sdf)

Stops at the first failing stage; JobReport.stages keeps every stage's feedback text and facts.
"""
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from assembly.builder import build_assembly
from assembly.kinematics import Tree
from description.checks import check_description
from export.urdf import export_package
from harness.tools import compile_part
from schemas.plan import AssemblyPlan
from schemas.signal import Signal
from validate.assembly_check import check_assembly
from validate.plan_check import check_plan
from validate.poses import grid
from validate.printability import check_print
from validate.statics import joint_loads

DESCRIPTION_POSES = (3, 9)   # grid samples per joint, max poses for D2


@dataclass
class JobReport:
    ok: bool = False
    failed_stage: str | None = None
    stages: dict = field(default_factory=dict)     # stage -> {ok, feedback, ...facts}
    signals: list[Signal] = field(default_factory=list)
    fcstd: dict = field(default_factory=dict)      # part -> compiled .FCStd
    assembly: str | None = None
    package: str | None = None
    urdf: str | None = None
    elapsed_s: float = 0.0

    def summary(self) -> dict:
        return {"ok": self.ok, "failed_stage": self.failed_stage, "elapsed_s": self.elapsed_s,
                "assembly": self.assembly, "package": self.package, "urdf": self.urdf,
                "stages": self.stages, "signals": [s.model_dump() for s in self.signals]}


def run_job(job_dir: Path, out_dir: Path | None = None, robot: str | None = None, description: bool = True) -> JobReport:
    """job_dir: plan.json + parts/*.py. out_dir (default job_dir/out): io/, build/, meshes/, release/."""
    t0 = time.monotonic()
    job_dir = Path(job_dir).resolve()
    out = Path(out_dir).resolve() if out_dir else job_dir / "out"
    io = out / "io"
    rep = JobReport()

    def stage(name, ok, sigs, feedback="", **facts):
        rep.stages[name] = {"ok": ok, "feedback": feedback, **facts}
        rep.signals += sigs
        if not ok and rep.failed_stage is None:
            rep.failed_stage = name
        return ok

    def done():
        rep.ok = rep.failed_stage is None
        rep.elapsed_s = round(time.monotonic() - t0, 3)
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.json").write_text(json.dumps(rep.summary(), indent=2, default=str), encoding="utf-8")
        return rep

    plan = AssemblyPlan.model_validate(json.loads((job_dir / "plan.json").read_text(encoding="utf-8")))
    pr = check_plan(plan)
    if not stage("plan", pr.ok, pr.signals, reach_mm=pr.reach_mm, tool=pr.tool, torques=pr.torques):
        return done()

    ok = True
    for spec in plan.parts:
        r = compile_part(job_dir / "parts" / f"{spec.name}.py", io, out=out / "build" / f"{spec.name}.FCStd", plan=plan)
        ok &= stage("part:" + spec.name, r.ok, r.signals, r.feedback, elapsed_s=r.elapsed_s,
                    volume_mm3=(r.report or {}).get("volume_mm3"), bound_box=(r.report or {}).get("bound_box"))
        if r.ok:
            rep.fcstd[spec.name] = r.fcstd
    if not ok:
        return done()

    ac = check_assembly(plan, rep.fcstd, io)
    if not stage("assembly", ac.ok, ac.signals, ac.feedback, reach_mm=ac.reach_mm, torques=ac.torques,
                 masses=ac.masses, collisions=ac.collisions, n_poses=ac.n_poses):
        return done()

    pc = check_print(plan, rep.fcstd, io, out / "meshes")
    if not stage("print", pc.ok, pc.signals, pc.feedback,
                 parts={k: {kk: v for kk, v in p.items() if kk != "stl"} for k, p in pc.parts.items()}):
        return done()

    ab = build_assembly(plan, rep.fcstd, io, out / "build" / "assembly.FCStd")
    rep.assembly = ab.fcstd
    if not stage("build", ab.ok, ab.signals, ab.feedback):
        return done()

    robot = robot or job_dir.name
    ex = export_package(plan, rep.fcstd, ac, io, out / "release", robot=robot)
    rep.package, rep.urdf = ex.package, ex.urdf
    if not stage("export", ex.ok, ex.signals, links=ex.links, joints=ex.joints):
        return done()

    if description:
        pd = plan.model_dump()
        tree = Tree(pd, ac.frames, ac.mounts)
        poses = grid(pd, *DESCRIPTION_POSES)
        masses = [(o, m["mass_kg"], m["com_mm"]) for o, m in ac.masses.items()]   # the URDF carries no payload
        expected = [joint_loads(tree, tree.joint_values(q)[0], masses) for q in poses]
        dc = check_description(ex.package, ex.urdf, poses, expected)
        stage("description", dc.ok, dc.signals, results=dc.results)
    return done()
