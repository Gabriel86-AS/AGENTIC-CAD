"""T1.11 bench runner without an LLM: case discovery, spec matching, and one scripted case end to end."""
import json

from bench.runner import cases, run_bench, spec_match, summary
from test_agents import scripted_backends


def test_cases_cover_p1_bench():
    cs = cases()
    assert [c["id"] for c in cs] == ["B1-1", "B1-2", "B1-3", "B1-4", "B1-5", "B1-6", "B1-7"]
    assert {c["id"]: c["group"] for c in cs}["B1-6"] == "core" and cs[0]["pack"] == "manipulator"
    assert all(c["prompt"] and "dof" in c["spec"] for c in cs)


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
