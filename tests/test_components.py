import math
import textwrap

import pytest

from components.build import build_card
from components.card import connectors, load_card, resolve
from harness.run import run_request


def test_resolve_param_names():
    card = load_card("servo/mg996r")
    p = card["params_mm"]
    assert connectors(card)["output_shaft"]["o"] == [p["shaft_x"], 0, p["spline_top"]]
    assert resolve(card, "-body_l") == -p["body_l"]
    assert resolve(card, "M4") == "M4"  # non-param strings pass through


def test_generic_bay_card_loads():
    card = load_card("electronics/generic_bay")
    assert card["model"] is None
    assert connectors(card)["bay_mount"]["z"] == [0, 0, 1]
    assert resolve(card, card["interfaces"]["keep_out"][0]["size"]) == [75.0, 60.0, 25.0]


@pytest.fixture(scope="module")
def mg996r(tmp_path_factory):
    return build_card("servo/mg996r", tmp_path_factory.mktemp("build"))


def test_mg996r_gates(mg996r):
    assert mg996r.ok, mg996r.signals
    assert mg996r.result["gates"] == {
        "sketches_fully_constrained": True, "one_solid": True,
        "no_invalid_objects": True, "connectors_match_card": True,
    }
    p = load_card("servo/mg996r")["params_mm"]
    expected = (p["body_l"] * p["body_w"] * (p["body_below"] + p["case_above"])
                + (p["flange_l"] - p["body_l"]) * p["body_w"] * p["flange_t"]
                - 4 * math.pi * (p["hole_d"] / 2) ** 2 * p["flange_t"]
                + math.pi * (p["spline_d"] / 2) ** 2 * (p["spline_top"] - p["case_above"]))
    assert mg996r.result["volume_mm3"] == pytest.approx(expected, abs=1e-6)


def test_mg996r_params_drive_geometry_and_connectors(mg996r, tmp_path):
    """Reopen the .FCStd, change shaft_x and spline_top: the spline and output_shaft LCS follow."""
    script = tmp_path / "change.py"
    script.write_text(textwrap.dedent("""
        import FreeCAD as App
        doc = App.openDocument(request["fcstd"])
        doc.getObject("Params").set(doc.getObject("Params").getCellFromAlias("shaft_x"), "=12 mm")
        doc.getObject("Params").set(doc.getObject("Params").getCellFromAlias("spline_top"), "=18 mm")
        doc.recompute()
        result["lcs"] = list(doc.getObject("output_shaft").getGlobalPlacement().Base)
        result["zmax"] = doc.getObject("Body").Shape.BoundBox.ZMax
        result["spline_axis_x"] = [f.Surface.Center.x for f in doc.getObject("Body").Shape.Faces
                                   if f.Surface.__class__.__name__ == "Cylinder" and f.BoundBox.ZMax > 11.2]
        result["invalid"] = [o.Name for o in doc.Objects if "Invalid" in o.State]
    """), encoding="utf-8")
    res = run_request({"op": "run_script", "script": str(script), "fcstd": mg996r.result["fcstd"]}, tmp_path / "io")
    assert res.ok, res.signals
    r = res.result
    assert r["invalid"] == []
    assert r["lcs"] == pytest.approx([12.0, 0.0, 18.0])
    assert r["zmax"] == pytest.approx(18.0)
    assert r["spline_axis_x"] and all(x == pytest.approx(12.0) for x in r["spline_axis_x"])
