"""T1.11 bench runner without an LLM: case discovery, spec matching, and one scripted case end to end."""
import json
import os

import pytest

import packs
from bench import runner
from agents import job as job_mod
from agents import stages
from bench.runner import cases, run_bench, spec_match, summarize, summary
from harness import tools as harness_tools
from test_agents import scripted_backends


@pytest.fixture(autouse=True)
def no_flex(monkeypatch):
    """L4 is tested in test_harness_tools / test_golden; here it would only re-flex the golden parts per compile."""
    monkeypatch.setattr(harness_tools, "FLEX", False)
    monkeypatch.setattr(stages, "LOOK", False)   # L3v needs a judge role; tested in test_vision
    monkeypatch.setattr(job_mod, "REVIEW", False)  # L8 needs a review role; tested in test_review
    monkeypatch.setattr(job_mod, "CLASSIFY", False)  # router v2 needs a router role; tested in test_router


def test_cases_cover_p1_bench():
    cs = cases()
    # pack cases (manipulator B1-1..5 + v1 B1-8..10, B2-1..12; mechanisms M2-1..8, T2.8), then the core-only ones
    assert [c["id"] for c in cs] == (["B1-%d" % i for i in range(1, 6)] + ["B1-%d" % i for i in range(8, 11)]
                                     + ["B2-%d" % i for i in range(1, 13)] + ["M2-%d" % i for i in range(1, 9)]
                                     + ["B1-6", "B1-7", "C2-1", "C2-2"])
    assert {c["id"]: c["group"] for c in cs}["B1-6"] == "core" and cs[0]["pack"] == "manipulator"
    assert all(c["prompt"] and "dof" in c["spec"] for c in cs)


def test_bench_v1_set_and_slices():
    """Phase file §4: 30 cases, 8 manipulator 2-DOF, 12 3-DOF, 8 mechanisms, 2 core-only (P1's B1-6/7 left out)."""
    v1 = cases(v1_only=True)
    n = {}
    for c in v1:
        n[c["slice"]] = n.get(c["slice"], 0) + 1
    assert len(v1) == 30 and n == {"manipulator_2dof": 8, "manipulator_3dof": 12, "mechanisms": 8, "core": 2}
    assert {c["id"] for c in v1 if c["slice"] == "core"} == {"C2-1", "C2-2"}
    assert all(c["spec"]["dof"] == 3 and c["spec"]["reach_mm"] for c in v1 if c["slice"] == "manipulator_3dof")


def test_core_cases_run_without_packs(tmp_path, monkeypatch):
    seen = {}

    def fake_run(prompt, job, backends, **kw):
        seen[prompt] = (os.environ.get(packs.ENV), packs.available())
        job.mkdir(parents=True, exist_ok=True)
        (job / "result.json").write_text(json.dumps({"status": "pass"}))

    monkeypatch.setattr(runner, "run", fake_run)
    monkeypatch.delenv(packs.ENV, raising=False)
    sel = [c for c in cases() if c["id"] in ("C2-1", "M2-1")]
    run_bench(sel, tmp_path / "r", lambda job: None, "scripted", description=False, results=tmp_path, log=lambda m: None)
    by_id = {c["id"]: seen[c["prompt"]] for c in sel}
    assert by_id["C2-1"] == ("", []) and by_id["M2-1"][0] is None and "mechanisms" in by_id["M2-1"][1]
    assert packs.ENV not in os.environ                   # restored after the core case
    assert packs.example(None)["golden"] == "b1_3"       # example lookup ignores the filter


def test_spec_match():
    target = {"pack": "manipulator", "dof": 2, "reach_mm": 150, "components_requested": ["servo/mg996r"],
              "electronics": ["electronics/esp32_devkit", "electronics/pca9685"], "notes": "ignored"}
    bom = {"cards": ["servo/mg996r", "electronics/esp32_devkit", "electronics/pca9685"]}
    spec = {"pack": "manipulator", "dof": 2, "reach_mm": 150.0, "components_requested": [],
            "electronics": ["electronics/pca9685", "electronics/esp32_devkit"], "notes": "other"}
    assert spec_match(target, spec, bom) == {"ok": True, "diffs": []}           # servo from the resolver, set order
    bad = spec_match(target, dict(spec, dof=3, electronics="generic"), {"cards": []})
    assert not bad["ok"] and len(bad["diffs"]) == 3
    assert spec_match({"pack": None, "dof": 1}, {"pack": "manipulator", "dof": 1}, {})["diffs"] == [
        "pack: want None, got 'manipulator'"]
    assert spec_match({"dof": 1}, None, None)["ok"] is False


def test_run_bench_scripted(tmp_path):
    case = next(c for c in cases() if c["id"] == "B1-3")
    logs = []
    doc = run_bench([case], tmp_path / "run1", lambda job: scripted_backends([]), "scripted",
                    description=False, results=tmp_path / "results", log=logs.append)
    row = doc["cases"][0]
    assert row["status"] == "pass" and row["spec_match"]["ok"], row
    assert row["stages"]["print"] is True and row["failed_stage"] is None
    assert row["dof0_rate"] == 1.0                     # the thin turret still compiles fully constrained
    assert row["compiles"] == 4 and row["rounds"] == 2 and row["parts"]["turret"]["sessions"] == 2
    assert row["cost_usd"] > 0 and row["sessions"] == 6
    saved = json.loads((tmp_path / "results" / "run1.json").read_text())
    assert saved["summary"]["passed"] == 1 and saved["summary"]["pack"] == {"passed": 1, "ran": 1, "need": "4/5"}
    assert saved["summary"]["p1_exit_bench"] is False  # one case is not the full bench
    again = run_bench([case], tmp_path / "run1", lambda job: 1 / 0, "scripted", description=False,
                      reuse=True, results=tmp_path / "results", log=logs.append)
    assert again["cases"][0]["status"] == "pass"       # reused: no backend called


def test_summary_exit_criterion():
    def row(i, g, ok):
        return {"id": i, "group": g, "status": "pass" if ok else "fail", "cost_usd": 1.0, "wall_s": 1.0,
                "tokens_in": 1, "tokens_out": 1, "spec_match": {"ok": True}, "dof0_rate": 1.0}
    rows = [row("B1-%d" % i, "pack", i != 5) for i in range(1, 6)] + [row("B1-6", "core", True), row("B1-7", "core", True)]
    assert summary(rows)["p1_exit_bench"] is True
    rows[-1]["status"] = "fail"
    assert summary(rows)["p1_exit_bench"] is False


def test_summary_v1_slices_and_bar():
    def row(c, ok, **kw):
        return dict({"id": c["id"], "group": c["group"], "slice": c["slice"], "status": "pass" if ok else "fail",
                     "cost_usd": 0.5, "wall_s": 10.0, "tokens_in": 10, "tokens_out": 1, "spec_match": {"ok": True},
                     "dof0_rate": 1.0, "rounds": 2, "compiles": 4, "sessions": 5, "l4": {"reached": 4, "passed": 3},
                     "torque_ok": ok, "print_ok": ok}, **kw)
    v1 = cases(v1_only=True)
    fails = {"B1-1", "B1-2", "B2-1", "B2-2", "B2-3", "B2-4", "M2-1", "M2-2", "M2-3"}   # 6/20 manip, 3/10 mech+core
    s = summary([row(c, c["id"] not in fails) for c in v1])
    m3 = s["slices"]["manipulator_3dof"]
    assert m3["cases"] == 12 and m3["passed"] == 8 and m3["success_rate"] == pytest.approx(0.667, abs=1e-3)
    assert m3["flex_pass_rate"] == 0.75 and m3["torque_ok"] == 8 and m3["cost_usd"] == 6.0 and m3["rounds_mean"] == 2
    assert s["v1_bar"]["manipulator"] == {"ran": 20, "rate": 0.7, "need": 0.7, "ok": True}
    assert s["v1_bar"]["mechanisms_core"]["rate"] == 0.7 and s["v1_exit_bench"] is True
    fails.add("C2-1")
    fails.add("C2-2")
    assert summary([row(c, c["id"] not in fails) for c in v1])["v1_exit_bench"] is False     # 5/10 < 60 %
    assert summary([row(v1[0], True)])["v1_exit_bench"] is False                                # not a full run


def test_summarize_reads_nested_open_failure_gates(tmp_path):
    case = next(c for c in cases() if c["id"] == "B1-1")
    (tmp_path / "result.json").write_text(json.dumps({"status": "fail", "open_failures": [
        {"part": "cam_head", "feedback": [{"level": "failure", "gate": "L5", "cause": "overlap"}]},
        {"gate": "L7", "cause": "wall"}]}))
    assert summarize(case, tmp_path)["open_failure_gates"] == ["L5", "L7"]
