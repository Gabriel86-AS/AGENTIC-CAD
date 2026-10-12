"""T2.7 router v2 without an LLM: ambiguity flags of the rule table, the budget table, candidate order, the
classifier tool, and job-level runs on a stand-in pipeline and compiler (fast): best-candidate return, one
escalation per part, a classifier that overrules the table, and one that cannot answer."""
import json
from pathlib import Path

import pytest

from agents import classify, stages
from agents import job as job_mod
from agents.budget import Budgets, next_step, score
from agents.job import run
from agents.router import Route, route
from harness import tools as harness_tools
from harness.pipeline import JobReport
from harness.tools import CompileResult
from schemas.signal import RouteDecision, Signal
from test_agents import B13, GOLDEN, PLAN, Scripted, golden_plan, sig

GOLDEN_SRC = {p.stem: p.read_text(encoding="utf-8") for p in (GOLDEN / "parts").glob("*.py")}
THIN = "\n# thin\n"                                   # stand-in pipeline: L7 thin wall on this part
BROKEN = "\nraise ValueError('oops')\n"              # stand-in compiler + pipeline: L1 failure


def fail(stage, sigs):
    return JobReport(ok=False, failed_stage=stage, signals=sigs)


# ---- rule table: what it is not sure of ---------------------------------------------------------
def test_repeat_after_code_repair_is_ambiguous():
    plan = golden_plan()
    first = route(fail("print", [sig("L7", "wall 0.8 mm", "parts/turret.py")]), plan)
    assert first.kind == "code" and not first.ambiguous
    again = route(fail("print", [sig("L7", "wall 0.9 mm", "parts/turret.py")]), plan, previous=first)
    assert again.kind == "code" and again.candidates == ["turret"]
    assert "turret: L7 parts/turret.py failed again after a CODE repair" in again.ambiguous[0]
    other = route(fail("print", [sig("L7", "wall 0.9 mm", "parts/arm.py")]), plan, previous=first)
    assert not other.ambiguous                                        # a different part: a new problem
    p1 = route(fail("part:arm", [sig("L1", "boom", "parts/arm.py:9")]), plan)
    p2 = route(fail("part:arm", [sig("L1", "bang", "parts/arm.py:31")]), plan, previous=p1)
    assert p2.ambiguous and p2.candidates == ["arm"]                  # line numbers do not make it new
    assert not route(fail("part:arm", [sig("L1", "boom", "parts/arm.py:9")]), plan,
                     previous=Route("design", signals=p1.signals)).ambiguous


@pytest.mark.parametrize("stage, sigs, kind, ambiguous, candidates", [
    ("assembly", [sig("L5", "assembly.solve() returned 3: arm frame off", "pose 2")], "design", True, ["arm"]),
    ("assembly", [sig("L5", "assembly.solve() returned 3", "pose 2")], "design", False, []),
    ("assembly", [sig("L5", "s_yaw and ebay overlap", "s_yaw <-> ebay")], "design", False, []),
    ("assembly", [sig("L6", "torque on arm too high", "plan.joints.j_shoulder")], "design", False, []),
    ("assembly", [sig("L5", "reach 120 < 150 (tool arm.tool)", "plan.params")], "design", False, []),
    ("print", [sig("L7", "fits the build volume in no orientation", "parts/arm.py")], "design", False, []),
    ("print", [sig("L7", "turret mesh has holes", "print")], "design", True, ["turret"]),
    ("print", [sig("L7", "thin wall", "parts/turret.py"),
               sig("L7", "arm fits the build volume in no orientation", "parts/arm.py")], "design", False, []),
])
def test_table_marks_what_it_is_not_sure_of(stage, sigs, kind, ambiguous, candidates):
    r = route(fail(stage, sigs), golden_plan())
    assert r.kind == kind and bool(r.ambiguous) == ambiguous and r.candidates == candidates


# ---- budget table ------------------------------------------------------------------------------
B = Budgets(part_rounds=2, design_rollbacks=1, max_rounds=4)


@pytest.mark.parametrize("kind, parts, stage, rounds, rollbacks, sessions, step, why", [
    ("pass", [], "", 4, 1, {}, "pass", ""),
    ("stop", [], "export", 1, 0, {}, "stop", ""),
    ("code", ["arm"], "print", 4, 0, {"arm": 1}, "best", "round budget"),
    ("code", ["arm"], "print", 2, 0, {"arm": 1}, "code", ""),
    ("code", ["arm", "base"], "assembly", 2, 0, {"arm": 1, "base": 2}, "design", "CODE budget spent on base"),
    ("code", ["arm"], "print", 3, 1, {"arm": 2}, "best", "part budget exhausted"),
    ("code", ["arm"], "part:arm", 2, 0, {"arm": 2}, "best", "part budget exhausted"),
    ("code", ["arm"], "L8", 2, 0, {"arm": 2}, "best", "part budget exhausted"),
    ("design", [], "assembly", 2, 0, {}, "design", ""),
    ("design", [], "assembly", 2, 1, {}, "best", "design rollback budget"),
])
def test_budget_table(kind, parts, stage, rounds, rollbacks, sessions, step, why):
    s = next_step(kind, parts, stage, rounds, rollbacks, sessions, B)
    assert s.kind == step and why in s.reason
    if step == "code":
        assert s.parts == parts


def test_candidate_order():
    def rep(stage, n_failed_stages=1, n_sigs=1):
        stages_ = {"plan": {"ok": True}, **{"part:p%d" % i: {"ok": False} for i in range(n_failed_stages)}}
        return JobReport(ok=False, failed_stage=stage, stages=stages_, signals=[sig("L7", "x")] * n_sigs)
    assert score(JobReport(ok=True)) > score(rep("description")) > score(rep("print")) > score(rep("assembly"))
    assert score(rep("assembly")) > score(rep("part:arm"))
    assert score(rep("part:arm", 1)) > score(rep("part:arm", 2))
    assert score(rep("print", 1, 1)) > score(rep("print", 1, 3))


# ---- classifier --------------------------------------------------------------------------------
def test_route_decision_schema():
    with pytest.raises(ValueError):
        RouteDecision(route="code", reason="x")
    assert RouteDecision(route="design", reason="layout").parts == []


def test_classifier_tool_and_route():
    plan = golden_plan()
    table = route(fail("print", [sig("L7", "wall 0.9 mm", "parts/turret.py")]), plan,
                  previous=route(fail("print", [sig("L7", "wall 0.8 mm", "parts/turret.py")]), plan))
    fails = [{"gate": "L7", "level": "failure", "cause": "wall 0.9 mm", "location": "parts/turret.py"},
             {"gate": "L5", "level": "failure", "cause": "arm and s_sh overlap", "location": "arm <-> s_sh"}]
    seen = {}

    def script(prompt, call):
        seen["prompt"] = prompt
        bad = call("submit_route", {"route": "code", "parts": ["s_sh"], "reason": "servo"})
        assert not bad["ok"] and "not plan parts" in bad["errors"][0]
        assert not call("submit_route", {"route": "code", "reason": "no parts"})["ok"]
        assert call("submit_route", {"route": "code", "parts": ["turret", "arm"], "reason": "thicken"})["ok"]

    hist = [{"round": 1, "kind": "code", "parts": ["turret"], "source": "table", "why": "print"}]
    r, err = classify.classify(plan, table, fails, Scripted("router", script), history=hist,
                               budgets="- pipeline runs: 2 of 6 used")
    assert not err and r.kind == "code" and r.source == "classifier" and r.reason == "classifier: thicken"
    assert r.parts["turret"] == [fails[0]] and r.parts["arm"] == [fails[1]]
    p = seen["prompt"]
    assert "failed again after a CODE repair" in p and "round 1: CODE turret (table)" in p
    assert "2 of 6 used" in p and "`arm_len` = 155 mm" in p and "`turret.horn_seat`" in p

    def quiet(prompt, call):
        return "I would rather not"
    same, err = classify.classify(plan, table, fails, Scripted("router", quiet))
    assert err and same is table

    d = classify.to_route(RouteDecision(route="design", reason="limits"), fails, table)
    assert d.kind == "design" and d.signals == fails and not d.parts


# ---- job level, stand-in pipeline --------------------------------------------------------------
def fake_compile(program, io_dir, out=None, part=None, plan=None, **kw):
    ok = BROKEN not in Path(program).read_text(encoding="utf-8")
    return CompileResult(ok, [], {"connectors": {}}, None, feedback="ok" if ok else "L1 ValueError: oops")


def fake_run_job(job_dir, out_dir=None, robot=None, description=True):
    """Part stage fails on BROKEN sources; print fails on THIN ones; else every stage passes."""
    srcs = {p.stem: p.read_text(encoding="utf-8") for p in sorted((job_dir / "parts").glob("*.py"))}
    rep = JobReport(stages={"plan": {"ok": True}})
    bad = [n for n, s in srcs.items() if BROKEN in s]
    rep.stages.update({"part:" + n: {"ok": n not in bad} for n in srcs})
    rep.signals = [Signal(level="failure", gate="L1", cause="ValueError: oops", location="parts/%s.py:7" % n) for n in bad]
    if not bad:
        thin = [n for n, s in srcs.items() if THIN in s]
        rep.stages.update(assembly={"ok": True}, print={"ok": not thin})
        rep.signals = [Signal(level="failure", gate="L7", cause="wall 0.8 mm < min_wall 1.2 mm",
                              location="parts/%s.py" % n) for n in thin]
    rep.failed_stage = next((k for k, st in rep.stages.items() if not st["ok"]), None)
    rep.ok = rep.failed_stage is None
    out = out_dir or job_dir / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(rep.summary()), encoding="utf-8")
    return rep


@pytest.fixture(autouse=True)
def stand_in(monkeypatch):
    monkeypatch.setattr(harness_tools, "compile_part", fake_compile)
    monkeypatch.setattr(job_mod, "run_job", fake_run_job)
    monkeypatch.setattr(stages, "LOOK", False)
    monkeypatch.setattr(job_mod, "REVIEW", False)


def backends(log, arm, router=None):
    """arm(n, role, revised) -> the arm source of its n-th session (0-based); other parts are golden."""
    designs = []

    def requirements(prompt, call):
        assert call("submit_spec", {"spec": {"pack": "manipulator", "dof": 2, "reach_mm": 150, "payload_g": 50,
                                             "components_requested": ["servo/mg996r"]}})["ok"]

    def design(prompt, call):
        plan = PLAN
        if designs:   # a revision rewords the arm intent, so the arm is regenerated
            plan = dict(PLAN, parts=[dict(q, intent=q["intent"] + " Revised.") if q["name"] == "arm" else q
                                     for q in PLAN["parts"]])
        designs.append(prompt)
        log.append(("design", len(designs)))
        assert call("submit_plan", {"plan": plan})["submitted"]

    def part(role):
        def script(prompt, call):
            name = prompt.split("parts/")[1].split(".py")[0]
            src = GOLDEN_SRC[name]
            if name == "arm":
                src = arm(sum(1 for e in log if e[0] == "arm"), role, len(designs) > 1)
            log.append((name, role))
            call("write_file", {"path": f"parts/{name}.py", "content": src})
        return script

    roles = {"requirements": requirements, "design": design, "part": part("part"),
             "part_escalated": part("part_escalated"), "router": router}
    return lambda role: Scripted(role, roles[role])


def _run(tmp_path, log, arm, router=None, **budgets):
    return run(B13, tmp_path / "job", backends(log, arm, router), approve=lambda t: True,
               budgets=Budgets(workers=3, **budgets), description=False, log=lambda m: None)


def test_exhausted_job_returns_the_best_candidate(tmp_path, monkeypatch):
    """Round 1 fails at print (thin arm); the repair breaks the compile (worse); the arm budget runs out ->
    the round 1 candidate is restored and reported with its open failures."""
    monkeypatch.setattr(job_mod, "CLASSIFY", False)
    log = []
    res = _run(tmp_path, log, lambda n, role, rev: GOLDEN_SRC["arm"] + (THIN if n == 0 else BROKEN), part_rounds=2,
               design_rollbacks=0)
    assert res.status == "fail" and res.rounds == 2
    assert res.best == {"round": 1, "failed_stage": "print", "restored": True}
    assert "part budget exhausted" in res.reason and "best candidate: round 1, failed at print" in res.reason
    assert [f["gate"] for f in res.open_failures] == ["L7"]
    assert (tmp_path / "job" / "parts" / "arm.py").read_text(encoding="utf-8").endswith(THIN)
    assert json.loads((tmp_path / "job" / "out" / "report.json").read_text())["failed_stage"] == "print"
    routes = [json.loads(l) for l in (tmp_path / "job" / "routes.jsonl").read_text().splitlines()]
    assert [(r["stage"], r["kind"], r["step"]) for r in routes] == [("print", "code", "code"),
                                                                     ("part:arm", "code", "best")]


def test_one_escalation_per_part(tmp_path, monkeypatch):
    monkeypatch.setattr(job_mod, "CLASSIFY", False)
    log = []
    res = _run(tmp_path, log, lambda n, role, rev: GOLDEN_SRC["arm"] + BROKEN, part_rounds=3, design_rollbacks=0)
    assert res.status == "fail" and res.rounds == 3
    assert [e[1] for e in log if e[0] == "arm"] == ["part", "part_escalated", "part", "part"]
    assert res.parts["arm"]["escalations"] == 1 and res.parts["arm"]["sessions"] == 4
    assert res.best == {"round": 3, "failed_stage": "part:arm", "restored": False}   # all equal: the latest


def test_classifier_overrules_a_repeated_code_route(tmp_path, monkeypatch):
    """Thin arm twice -> the table repeats CODE arm but is not sure; the classifier sends it to design; the
    revised plan regenerates the arm, which passes."""
    monkeypatch.setattr(job_mod, "CLASSIFY", True)
    log, prompts = [], []

    def router(prompt, call):
        prompts.append(prompt)
        assert call("submit_route", {"route": "design", "reason": "the arm contract leaves no room"})["ok"]

    res = _run(tmp_path, log, lambda n, role, rev: GOLDEN_SRC["arm"] + ("" if rev else THIN), router=router)
    assert res.status == "pass", (res.reason, res.open_failures)
    assert res.rollbacks == 1 and res.rounds == 3 and len(prompts) == 1
    assert res.router == {"routes": 2, "classified": 1, "overruled": 1, "errors": 0}
    assert "arm: L7 parts/arm.py failed again after a CODE repair of arm" in prompts[0]
    assert "pipeline runs: 2 of 6 used" in prompts[0] and "round 1: CODE arm (table)" in prompts[0]
    routes = [json.loads(l) for l in (tmp_path / "job" / "routes.jsonl").read_text().splitlines()]
    assert [(r["source"], r["kind"], r["step"]) for r in routes] == [("table", "code", "code"),
                                                                      ("classifier", "design", "design")]
    assert ("design", 2) in log


def test_classifier_without_answer_keeps_the_table_route(tmp_path, monkeypatch):
    monkeypatch.setattr(job_mod, "CLASSIFY", True)
    log = []
    res = _run(tmp_path, log, lambda n, role, rev: GOLDEN_SRC["arm"] + (THIN if n < 2 else ""),
               router=lambda prompt, call: "no idea")
    assert res.status == "pass" and res.rounds == 3 and res.rollbacks == 0
    assert res.router == {"routes": 2, "classified": 1, "overruled": 0, "errors": 1}
