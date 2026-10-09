"""SDK v0 unit tests (T1.2): one test per call; each asserts DoF = 0 and expression binding.

All cases run in one freecadcmd process (tests/freecad/sdk_cases.py); a test fails on its own case.
"""
import math
from pathlib import Path

import pytest

from components.card import load_card
from harness.run import run_request
from profiles import load_profile

CASES = Path(__file__).resolve().parent / "freecad" / "sdk_cases.py"
PROFILE = load_profile()


@pytest.fixture(scope="module")
def cases(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("sdk")
    res = run_request({"op": "run_script", "script": str(CASES), "out": str(tmp / "out")}, tmp / "io", timeout_s=120)
    assert res.ok, res.signals
    return res.result


def get(cases, name):
    c = cases[name]
    assert "error" not in c, c.get("traceback", c)
    return c


def assert_sound(c):
    """DoF 0 + every dimension bound, before and after the param change; all gates pass."""
    rep = c["report"]
    assert rep["signals"] == []
    assert all(rep["gates"].values()), rep["gates"]
    assert all(c["after_gates"].values()), c["after_gates"]
    for sketches in (rep["sketches"], c["after_sketches"]):
        for name, s in sketches.items():
            assert (s["solve"], s["dof"], s["fully"], s["unbound"]) == (0, 0, True, []), (name, s)


def vol(c, which="m0"):
    return c[which]["volume"]


def test_params_plan_binding(cases):
    c = get(cases, "params")
    assert_sound(c)
    params = c["report"]["params"]
    assert params["plan_w"]["source"] == "plan" and (params["plan_w"]["min"], params["plan_w"]["max"]) == (40, 80)
    assert params["depth"]["min"] == 20 and params["width"]["current"] == pytest.approx(60)
    assert vol(c) == pytest.approx(60 * 30 * 5)
    assert vol(c, "m1") == pytest.approx(70 * 30 * 5)  # plan param -> part param -> sketch


def test_datum_offsets_and_frames(cases):
    c = get(cases, "datum")
    assert_sound(c)
    x0, y0, z0, x1, y1, z1 = c["m0"]["bbox"]
    assert (z1, y0, x1) == pytest.approx((10 + 4, -22, 23))      # boss on top; XZ normal is -Y; frame datum at x=20
    x0, y0, z0, x1, y1, z1 = c["m1"]["bbox"]
    assert (z1, y0, x1) == pytest.approx((12 + 4, -21, 22))


def test_rect(cases):
    c = get(cases, "rect")
    assert_sound(c)
    assert c["m0"]["bbox"] == pytest.approx([0, 0, 0, 20, 10, 2])
    assert c["m1"]["bbox"] == pytest.approx([0, 0, 0, 24, 10, 2])  # cx 10 -> 12, w 20 -> 24


def test_circle(cases):
    c = get(cases, "circle")
    assert_sound(c)
    assert vol(c) == pytest.approx(math.pi * 25 * 2)
    assert vol(c, "m1") == pytest.approx(math.pi * 36 * 2)
    assert c["m1"]["bbox"][0] == pytest.approx(5 - 6)


def test_slot(cases):
    c = get(cases, "slot")
    assert_sound(c)
    area = lambda L, w: L * w + math.pi * (w / 2) ** 2
    assert vol(c) == pytest.approx(area(math.hypot(30, 10), 8) * 3)
    assert vol(c, "m1") == pytest.approx(area(math.hypot(40, 10), 10) * 3)


def test_polygon(cases):
    c = get(cases, "polygon")
    assert_sound(c)
    hexa = lambda R: 1.5 * math.sqrt(3) * R ** 2
    assert vol(c) == pytest.approx(hexa(10) * 2)
    assert vol(c, "m1") == pytest.approx(hexa(15) * 2)
    assert c["m0"]["bbox"][1] == pytest.approx(-10 * math.cos(math.pi / 6))  # flat edge at the bottom


def test_pad_pocket(cases):
    c = get(cases, "pad_pocket")
    assert_sound(c)
    base = 40 * 40 * 10 - math.pi * 9 * 10 + 4 * 6 * 2
    assert vol(c) == pytest.approx(base - 10 * 10 * 4)
    assert vol(c, "m1") == pytest.approx(base - 10 * 10 * 5)


def test_hole_fits_from_profile(cases):
    c = get(cases, "hole")
    assert_sound(c)
    clr, tap = PROFILE["fits"]["clearance"], PROFILE["screws"]["M3"]["tap"]
    hext = PROFILE["horizontal_hole_extra_mm"]
    a = lambda d: math.pi * (d / 2) ** 2
    removed = lambda f: 2 * a(3 + f) * 10 + a(tap) * 6 + a(3 + f + hext) * 40
    # the cross hole (x = 0, z = 5, along y) misses the vertical holes at (10, 10), (-10, -10), (10, -10)
    assert vol(c) == pytest.approx(40 * 40 * 10 - removed(clr))
    assert vol(c, "m1") == pytest.approx(40 * 40 * 10 - removed(0.4))
    p = c["report"]["params"]
    assert p["tap_m3"]["source"].startswith("profile:") and p["tap_m3"]["current"] == pytest.approx(tap)


def test_connector(cases):
    c = get(cases, "connector")
    assert_sound(c)
    tip = c["report"]["connectors"]["tip"]
    assert tip["o"] == pytest.approx([40, 0, 10]) and tip["z"] == pytest.approx([0, 1, 0])
    assert tip["x"] == pytest.approx([1, 0, 0]) and tip["label"] == "tool"
    assert c["after_connectors"]["tip"]["o"] == pytest.approx([55, 0, 8])
    assert c["report"]["connectors"]["bottom"]["z"] == pytest.approx([0, 0, -1])


def _mount_removed(p, clr, tap, body_l=None):
    body_l = body_l or p["body_l"]
    pocket = (body_l + clr) * (p["body_w"] + clr) * (p["body_below"] + clr / 2)
    holes = 4 * math.pi * (tap / 2) ** 2 * 2.5 * 4  # M4 tap, 2.5 x nominal deep
    keep_out = (35.35 - (body_l + clr) / 2) * 10 * 8  # cable-exit box x in [-35.35, -20.35] outside the pocket
    return pocket + holes + keep_out


def test_mount_card_interfaces(cases):
    c = get(cases, "mount")
    assert_sound(c)
    p = load_card("servo/mg996r")["params_mm"]
    clr, tap = PROFILE["fits"]["clearance"], PROFILE["screws"]["M4"]["tap"]
    block = 80 * 40 * 40
    assert vol(c) == pytest.approx(block - _mount_removed(p, clr, tap))
    assert vol(c, "m1") == pytest.approx(block - _mount_removed(p, clr, tap, body_l=42.7))
    params = c["report"]["params"]
    assert params["mg996r_body_l"]["source"] == "card:servo/mg996r"


def test_mount_plan_component_follows_connector(cases):
    c = get(cases, "mount_plan_component")
    assert_sound(c)
    assert "s_yaw_body_l" in c["report"]["params"]
    p = load_card("servo/mg996r")["params_mm"]
    removed = _mount_removed(p, PROFILE["fits"]["clearance"], PROFILE["screws"]["M4"]["tap"])
    assert vol(c, "m1") == pytest.approx(80 * 40 * 45 - removed)  # cuts move with the connector


def test_cable_channel(cases):
    c = get(cases, "cable_channel")
    assert_sound(c)
    assert vol(c) == pytest.approx(100 * 20 * 20 - math.pi * 9 * 80)
    assert vol(c, "m1") == pytest.approx(100 * 20 * 20 - math.pi * 9 * 70)


def test_save_reopen_keeps_expressions(cases):
    c = get(cases, "save_reopen")
    assert c["invalid"] == []
    assert c["lcs_z"] == pytest.approx(50)
    assert c["v1"] == pytest.approx(c["v0"] + 80 * 40 * 10)  # mount cuts moved up with the connector
    assert c["n_expr"] > 20


def test_part_program_imports_agenticcad(cases):
    c = get(cases, "program")
    assert_sound(c)
    assert c["material"] == "PLA"
    w, pin = 20, 5 + PROFILE["fits"]["press"]
    v = lambda L: (L * w + math.pi * (w / 2) ** 2 - 2 * math.pi * (pin / 2) ** 2) * 6
    assert vol(c) == pytest.approx(v(80)) and vol(c, "m1") == pytest.approx(v(100))
    assert c["after_connectors"]["tip"]["o"] == pytest.approx([100, 0, 6])


def test_errors_are_failure_signals(cases):
    c = get(cases, "errors")
    for k, sig in c.items():
        assert sig is not None, f"{k}: expected SDKError"
        assert sig["level"] == "failure" and sig["cause"], (k, sig)
    assert "unit symbol" in c["unit_alias"]["cause"]
    assert "s_missing" in c["unknown_component"]["cause"]
