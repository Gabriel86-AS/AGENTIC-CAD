"""T1.9 agents without an LLM: router table, plan excerpts, checkpoint, trace, and a whole job driven by a
scripted backend that replays the golden B1-3 (with one defective turret first, to exercise CODE repair)."""
import json
from pathlib import Path

import pytest

from agents import stages
from agents.context import checkpoint_text, part_excerpt
from agents.job import Budgets, changed_parts, run
from agents.router import route
from agents.trace import Trace, tokens
from harness.pipeline import JobReport
from llm.backend import LLMBackend, RunResult, ToolCall
from schemas.plan import AssemblyPlan, Spec
from schemas.signal import Signal
from validate.plan_check import check_plan

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "bench" / "golden" / "b1_3"
PLAN = json.loads((GOLDEN / "plan.json").read_text(encoding="utf-8"))
B13 = "A 2-DOF arm with a base yaw joint and a shoulder joint, 150 mm reach, carrying 50 g."


def golden_plan(**over) -> AssemblyPlan:
    return AssemblyPlan.model_validate({**PLAN, **over})


def sig(gate, cause, location="", level="failure"):
    return Signal(level=level, gate=gate, cause=cause, location=location)


# ---- router ----------------------------------------------------------------------------------
@pytest.mark.parametrize("stage, sigs, kind, parts", [
    ("plan", [sig("plan", "dof mismatch")], "design", []),
    ("part:arm", [sig("L2", "sketch beam DoF 1", "parts/arm.py:9")], "code", ["arm"]),
    ("assembly", [sig("L5", "turret and s_sh overlap", "turret <-> s_sh")], "code", ["turret"]),
    ("assembly", [sig("L5", "turret and arm overlap", "turret <-> arm")], "code", ["turret", "arm"]),
    ("assembly", [sig("L5", "s_yaw and ebay overlap", "s_yaw <-> ebay")], "design", []),
    ("assembly", [sig("L6", "static torque too high", "plan.joints.j_shoulder")], "design", []),
    ("assembly", [sig("L5", "reach 120 < 150", "plan.params")], "design", []),
    ("print", [sig("L7", "thin wall", "parts/turret.py")], "code", ["turret"]),
    ("print", [sig("L7", "fits the 180 mm build volume in no orientation", "parts/arm.py")], "design", []),
    ("export", [sig("export", "boom")], "stop", []),
])
def test_router_table(stage, sigs, kind, parts):
    r = route(JobReport(ok=False, failed_stage=stage, signals=sigs), golden_plan())
    assert r.kind == kind
    assert sorted(r.parts) == sorted(parts)


def test_router_pass():
    assert route(JobReport(ok=True), golden_plan()).kind == "pass"


def test_changed_parts():
    old = golden_plan()
    new = json.loads(json.dumps(PLAN))
    new["parts"][2]["intent"] = "longer arm"                             # arm spec changed
    new["joints"][0]["limits"]["upper"] = 1.0                             # j_yaw touches base? no: s_yaw -> turret
    new["params"]["arm_len"]["value"] = 160                               # params alone change nothing
    assert changed_parts(old, AssemblyPlan.model_validate(new)) == ["turret", "arm"]


# ---- context ---------------------------------------------------------------------------------
def test_part_excerpts_name_the_right_calls():
    plan = golden_plan()
    base, arm = part_excerpt(plan, "base"), part_excerpt(plan, "arm")
    assert "part.mount('s_yaw', at='servo_mount')" in base and "part.electronics_bay('ebay', at='ebay_mount')" in base
    assert 'part.mate_to("s_sh.horn", at=\'horn_seat\')' in arm and "servo_u_bracket('s_sh'" in arm
    assert "fixed to `turret`" in arm and "`P.arm_len` = 155" in arm
    assert "label='tool'" in arm


def test_checkpoint_lists_bom_joints_estimates():
    plan = golden_plan()
    bom = stages.resolve(plan.requirements)
    text = checkpoint_text(plan, check_plan(plan), bom)
    assert "servo/mg996r" in text and "x2" in text and "UNVERIFIED" in text
    assert "j_shoulder: s_sh drives arm (servo on turret), revolute -90.0° to 17.2°" in text
    assert "reach" in text and "~5.0 A" in text


def test_resolver_defaults():
    assert stages.resolve(Spec(pack="manipulator", dof=2))["cards"] == ["servo/mg996r", "electronics/generic_bay"]
    named = Spec(pack="manipulator", dof=2, electronics=["electronics/esp32_devkit", "electronics/pca9685"])
    assert stages.resolve(named)["cards"] == ["servo/mg996r", "electronics/esp32_devkit", "electronics/pca9685"]
    assert stages.resolve(Spec(dof=1))["cards"] == []                     # hinged box: core only, no bay


def test_trace_tokens_both_backends(tmp_path):
    assert tokens({"usage": {"input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 7},
                   "total_cost_usd": 0.02}) == (15, 7, 0.02)
    assert tokens({"input_tokens": 3, "output_tokens": 4}) == (3, 4, 0.0)
    t = Trace(tmp_path / "trace.jsonl")
    t.session("part:arm", RunResult("x", "m", "done", [ToolCall("compile_part", {}, "{}", False)],
                                    usage={"usage": {"input_tokens": 100, "output_tokens": 20}, "total_cost_usd": 0.5}))
    assert t.totals()["cost_usd"] == 0.5 and t.totals()["by_agent"]["part:arm"]["tokens_in"] == 100
    assert len((tmp_path / "trace.jsonl").read_text().splitlines()) == 2


# ---- a whole job with a scripted backend -----------------------------------------------------
class Scripted(LLMBackend):
    """Plays one role: `script(prompt, tools)` calls tools like a model would."""
    name = "scripted"

    def __init__(self, role, script):
        super().__init__(model="scripted-" + role)
        self.script = script

    def run(self, system, prompt, tools):
        calls = []
        by = {t.name: t for t in tools}

        def call(name, args):
            text, err = by[name].call(args)
            calls.append(ToolCall(name, args, text, err))
            return json.loads(text)

        final = self.script(prompt, call)
        return RunResult(self.name, self.model, final or "", calls, usage={"usage": {"input_tokens": 1000, "output_tokens": 100},
                                                                         "total_cost_usd": 0.01})


def scripted_backends(log, stubborn=False):
    """stubborn: the turret part agent keeps its defect until a design revision touches the turret."""
    designs = []
    golden = {p.stem: p.read_text(encoding="utf-8") for p in (GOLDEN / "parts").glob("*.py")}
    thin_turret = golden["turret"].replace("wall_t=8", "wall_t=5").replace("sh_y=12", "sh_y=12")   # M2 tap 0.1 mm from the wall face

    def requirements(prompt, call):
        bad = call("submit_spec", {"spec": {"dof": "two"}})
        assert not bad["ok"]
        assert call("submit_spec", {"spec": {"pack": "manipulator", "dof": 2, "reach_mm": 150, "payload_g": 50,
                                             "components_requested": ["servo/mg996r"]}})["ok"]
        return "spec submitted"

    def design(prompt, call):
        broken = dict(PLAN, dof=3)
        assert not call("check_plan", {"plan": broken})["ok"]
        plan = PLAN
        if designs:     # revision: reword the turret intent, so the turret is regenerated
            plan = dict(PLAN, parts=[dict(q, intent=q["intent"] + " Revised.") if q["name"] == "turret" else q
                                     for q in PLAN["parts"]])
        designs.append(prompt)
        assert call("submit_plan", {"plan": plan})["submitted"]
        return "plan submitted"

    def part(prompt, call):
        name = prompt.split("parts/")[1].split(".py")[0]
        log.append((name, "feedback" if "failed these checks" in prompt else "first"))
        src = golden[name]
        if name == "turret" and ("failed these checks" not in prompt or (stubborn and len(designs) < 2)):
            src = thin_turret
        call("write_file", {"path": f"parts/{name}.py", "content": src})
        assert call("compile_part", {})["ok"]
        return "done"

    roles = {"requirements": requirements, "design": design, "part": part, "part_escalated": part}
    return lambda role: Scripted(role, roles[role])


def test_job_end_to_end_with_code_repair(tmp_path):
    log = []
    res = run(B13, tmp_path / "job", scripted_backends(log), approve=lambda text: "CHECKPOINT" in text,
              budgets=Budgets(workers=3), description=False, log=lambda m: None)
    assert res.status == "pass", (res.reason, res.open_failures)
    assert res.rounds == 2 and res.rollbacks == 0                       # round 1: L7 thin wall -> CODE turret
    assert sorted(log) == [("arm", "first"), ("base", "first"), ("turret", "feedback"), ("turret", "first")]
    assert res.parts["turret"]["sessions"] == 2 and res.parts["arm"]["sessions"] == 1
    assert res.totals["sessions"] == 6 and res.totals["cost_usd"] == pytest.approx(0.06)
    job = tmp_path / "job"
    for f in ("spec.json", "bom.json", "plan.json", "checkpoint.txt", "trace.jsonl", "result.json", "context/arm.md"):
        assert (job / f).exists(), f
    assert json.loads((job / "result.json").read_text())["status"] == "pass"


def test_job_spent_code_budget_on_print_failure_rolls_back_design(tmp_path):
    log = []
    res = run(B13, tmp_path / "job", scripted_backends(log, stubborn=True), approve=lambda text: True,
              budgets=Budgets(workers=3, part_rounds=2), description=False, log=lambda m: None)
    assert res.status == "pass", (res.reason, res.open_failures)
    # L7 -> CODE turret; L7 again, budget spent -> DESIGN; regenerated turret L7 -> CODE (budget reset); pass
    assert res.rollbacks == 1 and res.rounds == 4
    assert res.parts["turret"]["sessions"] == 4 and res.parts["arm"]["sessions"] == 1


def test_job_aborts_at_checkpoint(tmp_path):
    res = run(B13, tmp_path / "job", scripted_backends([]), approve=lambda text: False, description=False,
              log=lambda m: None)
    assert res.status == "aborted" and not any((tmp_path / "job" / "parts").iterdir())
