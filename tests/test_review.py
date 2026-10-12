"""T2.6 L8 review without an LLM: verdict schema and routing, the review inputs on the golden B1-3 (renders,
joint keyframe sheets, GIFs, bbox table), the judge prompt, and the job-level L8 rounds with a scripted
reviewer (repair, persistent disagreement, a repair that breaks a gate is undone, design route)."""
import json
import shutil

import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from agents import job as job_mod
from agents import review, stages
from agents.job import Budgets, run
from harness import tools as harness_tools
from harness.pipeline import JobReport, run_job
from schemas.plan import AssemblyPlan
from schemas.review import ReviewVerdict
from schemas.signal import Signal
from test_agents import B13, GOLDEN, PLAN, Scripted
from validate import assembly_views
from validate.assembly_views import ReviewInputs, prepare

GOLDEN_SRC = {p.stem: p.read_text(encoding="utf-8") for p in (GOLDEN / "parts").glob("*.py")}
SCORES = {"geometry": 4, "detail": 3, "motion": 4, "reasons": {"geometry": "reads as an arm", "detail": "plain",
                                                                "motion": "both joints move the arm"}}
PASS = {"scores": SCORES, "issues": [{"severity": "minor", "where": "base", "what": "sharp corners", "route": "code",
                                      "parts": ["base"]}], "route_hint": "pass"}
CODE_ARM = {"scores": dict(SCORES, detail=2), "route_hint": "code", "issues": [
    {"severity": "major", "where": "arm", "what": "the beam is a bare slab; add lightening pockets", "route": "code",
     "parts": ["arm"]},
    {"severity": "minor", "where": "arm", "what": "round the tip", "route": "code", "parts": ["arm"]}]}
DESIGN = {"scores": dict(SCORES, geometry=2), "route_hint": "design", "issues": [
    {"severity": "major", "where": "ebay", "what": "the bay sits where the arm sweeps over it", "route": "design"}]}


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(harness_tools, "FLEX", False)
    monkeypatch.setattr(stages, "LOOK", False)
    monkeypatch.setattr(job_mod, "CLASSIFY", False)


# ---- schema and routing ------------------------------------------------------------------------
def test_verdict_consistency():
    assert ReviewVerdict.model_validate(PASS).route_hint == "pass"
    assert ReviewVerdict.model_validate(CODE_ARM).route_hint == "code"
    for bad in (dict(CODE_ARM, route_hint="pass"),                                   # major issue but pass
                dict(PASS, route_hint="code"),                                       # no major issue but code
                dict(DESIGN, route_hint="code"),                                     # a design issue wins
                dict(CODE_ARM, issues=[dict(CODE_ARM["issues"][0], parts=[])]),      # code issue names no part
                dict(PASS, scores=dict(SCORES, motion=6))):
        with pytest.raises(ValidationError):
            ReviewVerdict.model_validate(bad)
    mixed = dict(DESIGN, issues=DESIGN["issues"] + CODE_ARM["issues"])
    assert ReviewVerdict.model_validate(mixed).route_hint == "design"


def test_to_route():
    r = review.to_route(ReviewVerdict.model_validate(CODE_ARM))
    assert r.kind == "code" and list(r.parts) == ["arm"]
    assert [s["level"] for s in r.parts["arm"]] == ["failure", "warning"] and all(s["gate"] == "L8" for s in r.parts["arm"])
    d = review.to_route(ReviewVerdict.model_validate(DESIGN))
    assert d.kind == "design" and d.signals[0]["location"] == "ebay" and not d.parts
    assert review.to_route(ReviewVerdict.model_validate(PASS)).kind == "pass"


# ---- review inputs on the golden B1-3 ----------------------------------------------------------
@pytest.fixture(scope="module")
def golden_job(tmp_path_factory):
    harness_tools.FLEX = False
    try:
        job = tmp_path_factory.mktemp("l8") / "b1_3"
        shutil.copytree(GOLDEN, job)
        rep = run_job(job, job / "out", robot="b1_3", description=False)
    finally:
        harness_tools.FLEX = True
    assert rep.ok, rep.failed_stage
    return job, rep


def _img(p) -> np.ndarray:
    return np.asarray(Image.open(p).convert("RGB")).astype(int)


def test_review_inputs_golden(golden_job, tmp_path):
    job, rep = golden_job
    plan = AssemblyPlan.model_validate(PLAN)
    ri = prepare(plan, rep.fcstd, rep.frames, rep.mounts, rep.owners, job / "out" / "io", tmp_path)
    assert ri.ok, [s.cause for s in ri.signals]
    assert list(ri.renders) == ["iso", "iso_back", "front", "top"]
    assert {r["owner"] for r in ri.table} == {"base", "turret", "arm", "s_yaw", "s_sh", "ebay"}
    row = {r["owner"]: r for r in ri.table}
    assert row["s_sh"]["link"] == "turret" and row["ebay"]["link"] == "base" and row["arm"]["link"] == "arm"
    assert row["s_yaw"]["kind"] == "servo/mg996r" and row["s_yaw"]["color"] == assembly_views.CARD_COLORS["actuator"]
    assert len({row[p]["color"] for p in ("base", "turret", "arm")}) == 3          # one colour per link
    assert row["arm"]["size_mm"][0] == pytest.approx(170, abs=1)                  # world AABB at rest
    assert [s["joint"] for s in ri.sweeps] == ["j_yaw", "j_shoulder"]
    for s in ri.sweeps:
        sheet = _img(s["sheet"])
        assert sheet.shape == (1025, 1025, 3)
        tiles = [sheet[r:r + 512, c:c + 512] for r in (0, 513) for c in (0, 513)]
        assert all(np.abs(a - b).mean() > 0.5 for i, a in enumerate(tiles) for b in tiles[i + 1:]), s["joint"]   # it moves
        with Image.open(s["gif"]) as g:
            assert g.n_frames == 2 * assembly_views.FRAMES - 2                    # ping-pong
        assert s["keys"][0] == pytest.approx(s["lower"]) and s["keys"][-1] == pytest.approx(s["upper"])
    assert ri.images()[:4] == [ri.renders[v] for v in ("iso", "iso_back", "front", "top")] and len(ri.images()) == 6
    assert json.loads((tmp_path / "inputs.json").read_text())["table"] == ri.table


def test_review_prompt_and_judge(golden_job):
    job, rep = golden_job
    plan = AssemblyPlan.model_validate(PLAN)
    seen = {}

    def judge(prompt, call):
        seen["prompt"] = prompt
        bad = call("submit_verdict", dict(CODE_ARM, issues=[dict(CODE_ARM["issues"][0], parts=["s_sh"])]))
        assert not bad["ok"] and "not plan parts" in bad["errors"][0]
        assert call("submit_verdict", CODE_ARM)["ok"]
        return "done"

    backend = Scripted("review", judge)
    (job / "prompt.txt").write_text(B13, encoding="utf-8")
    rv = review.review(plan, rep, job, backend, round_no=1, parts_status={"arm": {"look": {"status": "warning", "issues": [
        {"severity": "major", "where": "beam", "what": "beam looks hollow"}]}}})
    assert rv.verdict is not None and rv.verdict.route_hint == "code"
    p = seen["prompt"]
    for s in (B13, "| arm | part | arm |", "j_shoulder", "driven by `s_sh`", "assembly:", "print:", "beam looks hollow",
              "sheet 6: `j_shoulder`"):
        assert s in p, s
    assert len(backend.images) == 6 and all(x.exists() for x in backend.images)
    assert (job / "review" / "round_1" / "sweeps" / "j_yaw.gif").exists()


# ---- job-level rounds --------------------------------------------------------------------------
def fake_prepare(plan, parts, frames, mounts, owners, io_dir, out_dir, sweeps=True):
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / "assembly_iso.png"
    Image.new("RGB", (8, 8), "white").save(png)
    return ReviewInputs(True, [], {"iso": png}, [], [{"owner": "arm", "kind": "part", "link": "arm", "color": "#fff",
                                                     "size_mm": [1, 1, 1], "center_mm": [0, 0, 0]}])


def job_backends(verdicts, log, arm_sources=(), design_same=False):
    """Golden B1-3 in one pass; the arm agent writes arm_sources[i] on its i-th L8 repair."""
    arm_sources = list(arm_sources)

    def requirements(prompt, call):
        assert call("submit_spec", {"spec": {"pack": "manipulator", "dof": 2, "reach_mm": 150, "payload_g": 50,
                                             "components_requested": ["servo/mg996r"]}})["ok"]

    def design(prompt, call):
        log.append(("design", "L8" in prompt))
        assert call("submit_plan", {"plan": PLAN})["submitted"]

    def part(prompt, call):
        name = prompt.split("parts/")[1].split(".py")[0]
        repair = "failed these checks" in prompt
        log.append((name, "L8" in prompt if repair else "first"))
        src = arm_sources.pop(0) if repair and name == "arm" and arm_sources else GOLDEN_SRC[name]
        call("write_file", {"path": f"parts/{name}.py", "content": src})
        call("compile_part", {})

    def judge(prompt, call):
        log.append(("review", len(verdicts)))
        v = verdicts.pop(0)
        if v is None:
            return "no verdict"
        assert call("submit_verdict", v)["ok"]

    roles = {"requirements": requirements, "design": design, "part": part, "part_escalated": part, "review": judge}
    return lambda role: Scripted(role, roles[role])


def fake_run_job(job_dir, out_dir=None, robot=None, description=True):
    """Stand-in pipeline (the real one costs ~40 s a run): a part whose source raises fails its compile."""
    rep = JobReport()
    bad = sorted(p.stem for p in (job_dir / "parts").glob("*.py") if "raise ValueError" in p.read_text(encoding="utf-8"))
    if bad:
        rep.failed_stage = "part:" + bad[0]
        rep.signals = [Signal(level="failure", gate="L1", cause="ValueError: oops", location="parts/%s.py:5" % bad[0])]
    rep.ok = not bad
    out = out_dir or job_dir / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(rep.summary()), encoding="utf-8")
    return rep


@pytest.fixture
def l8(monkeypatch):
    monkeypatch.setattr(job_mod, "REVIEW", True)
    monkeypatch.setattr(review, "prepare", fake_prepare)
    monkeypatch.setattr(job_mod, "run_job", fake_run_job)


@pytest.fixture
def l8_real(monkeypatch):
    monkeypatch.setattr(job_mod, "REVIEW", True)
    monkeypatch.setattr(review, "prepare", fake_prepare)


def _job(tmp_path, verdicts, log, **kw):
    return run(B13, tmp_path / "job", job_backends(verdicts, log, **kw), approve=lambda t: True,
               budgets=Budgets(workers=3), description=False, log=lambda m: None)


def test_l8_code_repair_then_persistent_disagreement_is_a_warning(tmp_path, l8):
    log = []
    edited = GOLDEN_SRC["arm"] + "\n# reviewed\n"
    res = _job(tmp_path, [CODE_ARM, CODE_ARM], log, arm_sources=[edited])
    assert res.status == "pass", (res.reason, res.open_failures)
    assert res.review["status"] == "warning" and res.review["rounds"] == 2 and "still disagrees" in res.review["reason"]
    assert res.rounds == 2 and res.parts["arm"]["sessions"] == 2 and res.parts["base"]["sessions"] == 1
    assert ("arm", True) in log                                              # the arm agent saw the L8 issues
    assert (tmp_path / "job" / "parts" / "arm.py").read_text(encoding="utf-8") == edited   # the repair is kept
    hist = json.loads((tmp_path / "job" / "review" / "review.json").read_text())
    assert [r["route_hint"] for r in hist["rounds"]] == ["code", "code"]
    assert json.loads((tmp_path / "job" / "result.json").read_text())["review"]["status"] == "warning"


def test_l8_repair_that_breaks_a_gate_is_undone(tmp_path, l8_real):
    """Real pipeline: the restore must leave sources and out/ consistent."""
    log = []
    broken = GOLDEN_SRC["arm"].replace("def build(P):", "def build(P):\n    raise ValueError('oops')", 1)
    assert broken != GOLDEN_SRC["arm"]
    res = _job(tmp_path, [CODE_ARM], log, arm_sources=[broken, broken])
    assert res.status == "pass", (res.reason, res.open_failures)
    assert res.review["status"] == "warning" and "kept the reviewed candidate" in res.review["reason"]
    assert (tmp_path / "job" / "parts" / "arm.py").read_text(encoding="utf-8") == GOLDEN_SRC["arm"]
    assert json.loads((tmp_path / "job" / "out" / "report.json").read_text())["ok"]   # out/ matches the restored sources
    assert [e for e in log if e[0] == "review"] == [("review", 1)]


def test_l8_design_route_then_pass(tmp_path, l8):
    log = []
    res = _job(tmp_path, [DESIGN, PASS], log)
    assert res.status == "pass" and res.review["status"] == "pass" and res.review["rounds"] == 2
    assert res.rollbacks == 1 and ("design", True) in log                   # the design agent got the L8 signals
    assert res.parts["arm"]["sessions"] == 1                                 # same plan: nothing regenerated


def test_l8_reviewer_without_verdict_does_not_block(tmp_path, l8):
    res = _job(tmp_path, [None], [])
    assert res.status == "pass" and res.review["status"] == "skipped" and res.rounds == 1
