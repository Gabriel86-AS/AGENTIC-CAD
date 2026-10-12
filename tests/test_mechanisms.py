"""T2.8 mechanisms pack composites (hinge, slide_rail, spur_gear, snap_hook): each pair built as the plan places
it at rest, in one freecadcmd run (tests/freecad/mech_cases.py). Every half: DoF 0, every dimension bound, L4
flex clean, the param edit recomputes; the pair: no overlap at rest and along / about its joint."""
import math
from pathlib import Path

import pytest

import packs
from harness.run import run_request
from profiles import load_profile

CASES = Path(__file__).resolve().parent / "freecad" / "mech_cases.py"
PROFILE = load_profile()
CLR = PROFILE["fits"]["clearance"]


@pytest.fixture(scope="module")
def cases(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("mech")
    res = run_request({"op": "run_script", "script": str(CASES), "out": str(tmp / "out")}, tmp / "io", timeout_s=900)
    assert res.ok, res.signals
    return res.result


def get(cases, name):
    c = cases[name]
    assert "error" not in c, c.get("traceback", c)
    return c


def assert_sound(c):
    rep = c["report"]
    assert rep["signals"] == [] and all(rep["gates"].values()), rep["gates"]
    assert all(c["after_gates"].values()), c["after_gates"]
    for sketches in (rep["sketches"], c["after_sketches"]):
        for name, s in sketches.items():
            assert (s["solve"], s["dof"], s["fully"], s["unbound"]) == (0, 0, True, []), (name, s)
    f = c["flex"]
    assert f["runs"] > 0 and f["restored"] and f["failures"] == [], f


def test_pack_metadata():
    assert "mechanisms" in packs.available()
    meta = packs.meta("mechanisms")
    assert meta["composites"] == ["hinge", "slide_rail", "spur_gear", "snap_hook"]
    assert "hinge(" in packs.knowledge("mechanisms") and len(packs.bench("mechanisms")) == 8


def test_hinge_pair(cases):
    c = get(cases, "hinge_pair")
    assert c["ret"] == {"length": 30, "od": 8, "gap": 0.5, "seg": 10}
    for half in ("a", "b"):
        assert_sound(c[half])
    assert c["a"]["m1"]["volume"] > c["a"]["m0"]["volume"]                    # od 8 -> 9
    assert c["b"]["m1"]["volume"] < c["b"]["m0"]["volume"]                    # gap 0.5 -> 1: shorter knuckle
    assert all(v == pytest.approx(0, abs=1e-6) for v in c["common"].values()), c["common"]   # 0, 45, 90, -90 deg
    # a: knuckles 0 and 2 (z 0..9.75, 20.25..30) + leaf; b: knuckle 1 (z 10.25..19.75) + leaf; pin bore on the axis
    assert c["probes_a"] == [True, False, False, True, True]
    assert c["probes_b"] == [False, True, False, True]
    tap, through = PROFILE["screws"]["M3"]["tap"], PROFILE["screws"]["M3"]["clearance"]
    assert c["holes_a"] == [tap, 8] and c["holes_b"] == [through, 8]          # a holds the screw, b turns on it


def test_hinge_relief_and_filament_pin(cases):
    c = get(cases, "hinge_relief")
    assert_sound(c)
    # relief r = (6 + 2 x 0.5) / 2 in segment 1 only; own knuckles keep the plate; filament bore press-fit
    assert c["probes"] == [False, False, True, True, False]
    assert c["holes"] == [1.75 + PROFILE["fits"]["press"], 6.0, 7.0]


def test_slide_rail_pair(cases):
    c = get(cases, "slide_pair")
    for half in ("a", "b"):
        assert_sound(c[half])
    assert c["clearance"] == CLR
    # rail: dovetail 8 -> 10 wide, 4 high, 40 long on a 30 x 10 x 40 carriage; height 5 adds a 9 x 1 x 40 band
    assert c["a"]["m0"]["volume"] == pytest.approx(30 * 10 * 40 + (8 + 10) / 2 * 4 * 40)
    assert c["a"]["m1"]["volume"] == pytest.approx(30 * 10 * 40 + (8 + 10) / 2 * 5 * 40)
    # groove: rail + clearance (half per side and on top), through the 80 mm frame
    hw, top = 4 + CLR / 2, 4 + CLR / 2
    assert c["b"]["m0"]["volume"] == pytest.approx(30 * 12 * 80 - (2 * hw + 2 * hw + 2) / 2 * top * 80)
    assert all(v == pytest.approx(0, abs=1e-6) for v in c["common"].values()), c["common"]   # dz 0, +-20
    assert c["probes_b"] == [False, False, True, False, True]
    assert c["probes_a"] == [True, False, True]
    assert c["flat_common"] == pytest.approx(0, abs=1e-6)
    assert c["flat_a"]["m0"]["volume"] == pytest.approx(30 * 10 * 40 + 8 * 4 * 40)


def test_spur_gear_pair_meshes(cases):
    c = get(cases, "gear_pair")
    for half in ("a", "b"):
        assert_sound(c[half])
    assert c["ret"] == pytest.approx({"module": 1.5, "teeth": 30, "thick": 6, "pitch_d": 45, "tip_d": 48,
                                      "root_d": 41.25})
    assert "Part::Part2DObjectPython" in c["types"]                          # PartDesign InvoluteGear profile
    # driver 12 / follower 30 at m (z1 + z2) / 2 + 0.1, follower at -z1/z2 over +-30 deg: no overlap; a tooth
    # instead of a gap toward the driver collides
    assert max(c["mesh_common"].values()) < 1e-3, c["mesh_common"]
    assert c["wrong_phase_common"] > 10
    assert c["tooth_on_x"] and c["b_gap_toward_a"]
    assert c["a"]["m0"]["bbox"][3] == pytest.approx(1.5 * 14 / 2, abs=0.05)  # tip circle
    assert c["a"]["m1"]["volume"] > 1.5 * c["a"]["m0"]["volume"]             # module 1.5 -> 2 re-cuts the teeth
    assert PROFILE["screws"]["M3"]["clearance"] in c["holes"]


def test_snap_hook_pair(cases):
    c = get(cases, "snap_pair")
    for half in ("a", "b"):
        assert_sound(c[half])
    r = c["ret"]
    land = PROFILE["min_wall_mm"]
    lh = 1 / math.tan(math.radians(30)) + land
    assert r["lip_h"] == pytest.approx(lh)
    assert r["strain"] == pytest.approx(1.5 * 2 * 1 / (15 - lh) ** 2)
    # hook: 10 x 16 x 5 lid + 2 x 15 x 8 beam + lip (1 x land + ramp triangle) x 8; catch: window lip + fit
    assert c["a"]["m0"]["volume"] == pytest.approx(10 * 16 * 5 + 2 * 15 * 8 + (land + (lh - land) / 2) * 1 * 8)
    assert c["b"]["m0"]["volume"] == pytest.approx(3 * 20 * 18 - (lh + CLR) * (8 + CLR) * (1 + CLR))
    assert c["common"] == pytest.approx(0, abs=1e-6)
    assert c["probes_a"] == [True, False, True, False, True]
    assert c["probes_b"] == [False, True, True, True]


def test_errors(cases):
    c = get(cases, "errors")
    assert "side must be 'a' or 'b'" in c["hinge_side"]["cause"]
    assert "knuckles must be an int >= 2" in c["hinge_knuckles"]["cause"]
    assert "pin must be one of" in c["hinge_pin"]["cause"]
    assert "knuckle segment 0.4 mm <= gap" in c["hinge_short"]["cause"]
    assert "needs a length" in c["rail_no_length"]["cause"]
    assert "whole number >= 8" in c["gear_teeth"]["cause"]
    assert "does not print" in c["gear_module"]["cause"]
    assert "bending strain" in c["snap_strain"]["cause"] and "> 3.0 % allowed for PETG" in c["snap_strain"]["cause"]
    assert "allowed for PLA" in c["snap_pla"]["cause"] and c["snap_petg_ok"]["cause"] == "no error"
