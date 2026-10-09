"""T1.5: L0 lint, signal formatter, plan-side L3, and compile_part / probe_part through the worker."""
import math

import pytest

from harness.feedback import format_feedback
from harness.lint import lint_probe, lint_program
from harness.tools import check_against_plan, compile_part, probe_part
from schemas.plan import AssemblyPlan
from schemas.signal import Signal

LINK = '''from agenticcad import Part


def build(P):
    part = Part("link", material="PETG")
    p = part.params(length=P.link_len, width=20, thick=6)
    s = part.sketch("profile", on="XY").slot((0, 0), (p.length, 0), p.width)
    part.pad(s, p.thick)
    part.hole("XY", at=(p.length, 0), d=5, fit="press", name="pin")
    part.connector("tip", origin=(p.length, 0, p.thick), z=(0, 0, 1), x=(1, 0, 0), label="tool")
    return part
'''


def make_plan(tip=(80, 0, 6), label="tool", bbox=(100, 20, 6)):
    return AssemblyPlan.model_validate({
        "ground": "link", "requirements": {"dof": 0}, "dof": 0,
        "params": {"link_len": {"value": 80, "min": 60, "max": 120}},
        "parts": [{"name": "link", "intent": "flat bar with a pin hole", "bbox_mm": list(bbox),
                   "connectors": [{"name": "tip", "o": list(tip), "z": [0, 0, 1], "x": [1, 0, 0], "label": label}]}],
    })


# ---- L0 -------------------------------------------------------------------------------------
def test_lint_accepts_sdk_program():
    assert lint_program(LINK, "parts/link.py") == []


@pytest.mark.parametrize("src, needle", [
    ("import os\ndef build(P):\n    pass\n", "import of 'os'"),
    ("from FreeCAD import Vector\ndef build(P):\n    pass\n", "import from 'FreeCAD'"),
    ("def build(P):\n    open('x')\n", "'open'"),
    ("def build(P):\n    return App.ActiveDocument\n", "'App'"),
    ("def build(P):\n    return P.__class__.__subclasses__()\n", "'__subclasses__'"),
    ("from agenticcad import Part\ndef build(P):\n    return Part('a').doc\n", "'doc'"),
    ("from agenticcad import Part\n", "no top-level `def build(P)`"),
    ("def build():\n    pass\n", "exactly one argument"),
    ("def build(P)\n    pass\n", "syntax error"),
])
def test_lint_rejects(src, needle):
    sigs = lint_program(src, "parts/x.py")
    assert sigs and all(s.gate == "L0" and s.level == "failure" for s in sigs)
    assert any(needle in s.cause for s in sigs), [s.cause for s in sigs]
    assert all(s.location.startswith("parts/x.py:") for s in sigs)


def test_lint_probe():
    assert lint_probe("probe.thickness((0, 0, 3), (0, 0, 1)) * math.cos(0)") == []
    assert lint_probe("from agenticcad import Part\nPart('x')")[0].cause.startswith("import from")
    assert lint_probe("probe._doc")[0].cause.startswith("attribute '_doc'")


# ---- formatter --------------------------------------------------------------------------------
def test_feedback_orders_merges_and_caps():
    sigs = [Signal(level="note", gate="L3", cause="n"),
            Signal(level="failure", gate="L2", cause="sketch a: DoF 2", location="parts/x.py:4", action="constrain"),
            Signal(level="failure", gate="L2", cause="sketch a: DoF 2", location="parts/x.py:4", action="constrain"),
            Signal(level="warning", gate="L3", cause="w " + "x" * 500)]
    text = format_feedback("compile_part", sigs, max_signals=2)
    lines = text.splitlines()
    assert lines[0] == "compile_part: FAIL (1 failure, 1 warning, 1 note)"
    assert lines[1] == "- FAILURE L2: sketch a: DoF 2 | at parts/x.py:4 | fix: constrain"
    assert lines[2].startswith("- WARNING L3: w x") and lines[2].endswith("...") and len(lines[2]) < 330
    assert lines[3].startswith("(+1 more")
    assert format_feedback("compile_part", [], "volume 1 mm3") == "compile_part: PASS - volume 1 mm3"


# ---- plan-side L3 -----------------------------------------------------------------------------
def _report(o=(80, 0, 6), label="tool", bb=(-10, -10, 0, 90, 10, 6)):
    return {"material": "PETG", "bound_box": list(bb),
            "connectors": {"tip": {"o": list(o), "z": [0, 0, 1], "x": [1, 0, 0], "label": label}}}


def test_plan_checks_pass_within_tolerance():
    assert check_against_plan(_report(o=(80.005, 0, 6)), make_plan().part("link")) == []


def test_plan_checks_catch_pose_label_bbox_and_missing():
    spec = make_plan().part("link")
    sigs = check_against_plan(_report(o=(80.5, 0, 6), label="mount", bb=(-10, -10, 0, 130, 10, 6)), spec)
    causes = " / ".join(s.cause for s in sigs)
    assert [s.level for s in sigs] == ["failure"] * 3, causes
    assert "the plan declares o=[80.0, 0.0, 6.0]" in causes and "label 'mount'" in causes and ": x off" in causes
    assert sigs[0].evidence["o_mm"] == pytest.approx(0.5)
    missing = check_against_plan({"connectors": {}, "bound_box": None}, spec)
    assert missing[0].cause == "plan connector 'tip' is missing" and "part.connector('tip'" in missing[0].action


# ---- worker integration ------------------------------------------------------------------------
def write(tmp_path, src, name="link"):
    p = tmp_path / "parts" / f"{name}.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(src, encoding="utf-8")
    return p


@pytest.fixture(scope="module")
def compiled(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("compile")
    return tmp, compile_part(write(tmp, LINK), tmp / "io", plan=make_plan())


def test_compile_part_pass(compiled):
    _, res = compiled
    assert res.ok, res.feedback
    assert res.feedback.startswith("compile_part: PASS - volume ")
    pin = 5 + 0.2  # press fit from the profile
    v = (80 * 20 + math.pi * 10 ** 2 - math.pi * (pin / 2) ** 2) * 6
    assert res.report["volume_mm3"] == pytest.approx(v)
    out = res.for_agent()
    assert out["connectors"]["tip"]["o"] == pytest.approx([80, 0, 6]) and "link_len" in out["params"]
    assert res.report["params"]["link_len"]["source"] == "plan" and res.fcstd.endswith("link.FCStd")


def test_compile_part_plan_mismatch(tmp_path):
    res = compile_part(write(tmp_path, LINK), tmp_path / "io", plan=make_plan(tip=(80, 0, 0)))
    assert not res.ok
    assert "FAILURE L3: connector 'tip' is at o=[80.0, 0.0, 6.0]" in res.feedback


def test_compile_part_sdk_error_has_program_line(tmp_path):
    src = LINK.replace("slot((0, 0), (p.length, 0), p.width)", "slot((0, 0), (0, 0), p.width)")
    res = compile_part(write(tmp_path, src), tmp_path / "io", plan=make_plan())
    assert not res.ok and res.report is None and res.fcstd is None
    s = res.signals[0]
    assert s.level == "failure" and "start and end coincide" in s.cause
    assert s.location.startswith("parts/link.py:7")


def test_compile_part_python_error_and_bad_return(tmp_path):
    res = compile_part(write(tmp_path, LINK.replace("p.thick)\n    part.hole", "p.thikc)\n    part.hole")),
                       tmp_path / "io", plan=make_plan())
    assert res.signals[0].gate == "L1" and "thikc" in res.signals[0].cause
    assert res.signals[0].location == "parts/link.py:8"
    res = compile_part(write(tmp_path, LINK.replace("return part", "return None")), tmp_path / "io2",
                       plan=make_plan())
    assert "build(P) returned NoneType, not a Part" in res.feedback


def test_compile_part_l0_never_reaches_worker(tmp_path):
    res = compile_part(write(tmp_path, "import os\n" + LINK), tmp_path / "io")
    assert not res.ok and res.signals[0].gate == "L0" and "stopped at L0" in res.feedback
    assert not (tmp_path / "io").exists()


def test_probe_part_measures(compiled):
    tmp, res = compiled
    snippet = ("{'bbox': probe.bbox()['size'], 'tip': probe.connector('tip')['o'],"
               " 'wall': probe.thickness((40, 0, 3), (0, 0, 1)), 'hole': probe.inside((80, 0, 3)),"
               " 'solid': probe.inside((40, 0, 3)), 'segs': probe.segments((-20, 0, 3), (100, 0, 3)),"
               " 'len': probe.param('link_len'), 'dof': [s['dof'] for s in probe.sketches().values()]}")
    out = probe_part(res.fcstd, snippet, tmp / "io")
    assert out.ok, out.feedback
    v = out.value
    assert v["bbox"] == pytest.approx([100, 20, 6]) and v["tip"] == pytest.approx([80, 0, 6])
    assert v["wall"] == pytest.approx(6) and (v["hole"], v["solid"]) == (False, True)
    r = 5.2 / 2  # material from x=-10 to the pin hole, then from the hole to x=90
    assert [t for seg in v["segs"] for t in seg] == pytest.approx([10, 100 - r, 100 + r, 110])
    assert v["len"] == pytest.approx(80) and set(v["dof"]) == {0}


def test_probe_part_errors(compiled):
    tmp, res = compiled
    out = probe_part(res.fcstd, "probe.connector('nope')", tmp / "io")
    assert not out.ok and "no connector 'nope'" in out.feedback and "probe.help()" in out.feedback
    out = probe_part(res.fcstd, "import os", tmp / "io")
    assert not out.ok and out.signals[0].gate == "L0"
