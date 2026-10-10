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
    res = run_request({"op": "run_script", "script": str(CASES), "out": str(tmp / "out")}, tmp / "io", timeout_s=600)
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
    assert c["self_alias_expr"] == "Params.plan_w"  # params(plan_w=P.plan_w) re-exports, no redefinition


def test_datum_offsets_and_frames(cases):
    c = get(cases, "datum")
    assert_sound(c)
    x0, y0, z0, x1, y1, z1 = c["m0"]["bbox"]
    assert (z1, y0, x1) == pytest.approx((10 + 4, -22, 23))      # boss on top; XZ normal is -Y; frame datum at x=20
    x0, y0, z0, x1, y1, z1 = c["m1"]["bbox"]
    assert (z1, y0, x1) == pytest.approx((12 + 4, -21, 22))
    # orientation is bound too: z=(1,0,0), x=(0,1,0) -> yaw 90, roll 90 (pitch 0 stays a literal)
    assert sorted(c["tilt_angles"]) == ["tilt_roll", "tilt_yaw"]
    assert c["tilt_z"] == pytest.approx([0, 1, 0], abs=1e-9)  # yaw 90 -> 180 turns the datum normal


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
    s = math.sqrt(0.5)
    after = c["after_connectors"]
    assert after["tip"]["z"] == pytest.approx([0, s, s]) and after["tip"]["x"] == pytest.approx([1, 0, 0])
    assert c["report"]["connectors"]["side"]["x"] == pytest.approx([0, 1, 0], abs=1e-9)  # rot=(90, 0, 0)
    assert after["side"]["x"] == pytest.approx([-1, 0, 0], abs=1e-9)
    assert after["side"]["z"] == pytest.approx([0, 0, 1], abs=1e-9)


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


def test_mount_turns_with_connector(cases):
    c = get(cases, "mount_rotated")
    assert_sound(c)
    p = load_card("servo/mg996r")["params_mm"]
    removed = _mount_removed(p, PROFILE["fits"]["clearance"], PROFILE["screws"]["M4"]["tap"])
    assert vol(c) == pytest.approx(80 * 80 * 40 - removed)
    assert vol(c, "m1") == pytest.approx(80 * 80 * 40 - removed)  # same cuts, now on the +y face
    side = c["after_connectors"]["side_mount"]
    assert side["o"] == pytest.approx([0, 40, 20]) and side["z"] == pytest.approx([0, 1, 0], abs=1e-9)


def test_cable_channel(cases):
    c = get(cases, "cable_channel")
    assert_sound(c)
    assert vol(c) == pytest.approx(100 * 20 * 20 - math.pi * 9 * 80)
    assert vol(c, "m1") == pytest.approx(100 * 20 * 20 - math.pi * 9 * 70)
    assert all(c["tilted_gates"].values())
    assert vol(c, "m2") == pytest.approx(100 * 20 * 20 - math.pi * 9 * math.hypot(80, 4))  # end z 10 -> 14


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
    assert "read it as P.plate_len" in c["plan_redefined"]["cause"]
    assert "s_missing" in c["unknown_component"]["cause"]
    assert "not orthogonal" in c["not_orthogonal"]["cause"]
    assert "host thickness" in c["slotted_no_host"]["cause"]
    assert "disc horns at: ['horn']" in c["mate_no_horn"]["cause"]
    assert "clearance fit" in c["counterbore_tap"]["cause"]
    assert "not an electronics card" in c["bay_not_electronics"]["cause"]
    assert "not a servo" in c["u_bracket_not_servo"]["cause"]


def test_mate_to_disc_horn(cases):
    """Recess (disc + clearance), M2 tap holes into the part above it, screwdriver access through."""
    c = get(cases, "mate_to")
    assert_sound(c)
    p = load_card("servo/mg996r")["params_mm"]
    clr, tap = PROFILE["fits"]["clearance"], PROFILE["screws"]["M2"]["tap"]
    a = lambda d: math.pi * (d / 2) ** 2

    def removed(disc_t):
        ceiling = disc_t + clr / 2  # part bottom at z = 0, recess from the bottom face up
        return a(p["horn_disc_d"] + clr) * ceiling + 4 * a(tap) * 2.5 * 2 + a(p["horn_access_d"]) * (10 - ceiling)

    assert vol(c) == pytest.approx(60 * 40 * 10 - removed(p["horn_disc_t"]))
    assert vol(c, "m1") == pytest.approx(60 * 40 * 10 - removed(3))
    assert c["report"]["params"]["s_shoulder_horn_disc_d"]["source"] == "card:servo/mg996r"


def _slot(L, w):
    return L * w + math.pi * (w / 2) ** 2


def test_electronics_bay_generic(cases):
    """Floor + walls around the usable volume, slotted standoffs with nut traps, two wall openings."""
    c = get(cases, "bay_generic")
    assert_sound(c)
    b = load_card("electronics/generic_bay")["params_mm"]
    clr, wall, min_wall = PROFILE["fits"]["clearance"], 2 * PROFILE["min_wall_mm"], PROFILE["min_wall_mm"]

    def expected(margin, nut_m):
        il, iw = b["usable_l"] + 2 * margin, b["usable_w"] + 2 * margin
        ol, ow = il + 2 * wall, iw + 2 * wall
        floor = max(3, nut_m + clr / 2 + min_wall)
        h = b["standoff_h"]
        v = ol * ow * floor + (ol * ow - il * iw) * (h + b["usable_h"]) + 4 * _slot(b["slot_len"], b["standoff_d"]) * h
        v -= 4 * _slot(b["slot_len"], b["slot_w"]) * (h + floor)
        v -= 4 * (_slot(b["slot_len"], 5.5 + clr) - _slot(b["slot_len"], b["slot_w"])) * (nut_m + clr / 2)
        v -= (b["cable_exit_w"] * b["cable_exit_h"] + b["connector_cut_w"] * b["connector_cut_h"]) * wall
        return v

    assert vol(c) == pytest.approx(expected(2, 2.4))
    assert vol(c, "m1") == pytest.approx(expected(3, 3.0))  # margin and M3 nut thickness edited
    assert c["floor_t"]["before"] == pytest.approx(3.9) and c["floor_t"]["after"] == pytest.approx(4.5)


def test_electronics_bay_pca9685(cases):
    c = get(cases, "bay_pca9685")
    assert_sound(c)
    b = load_card("electronics/pca9685")["params_mm"]
    tap, wall = PROFILE["screws"]["M2.5"]["tap"], 2 * PROFILE["min_wall_mm"]

    def expected(h):
        il, iw = b["pcb_l"] + 4, b["pcb_w"] + 4
        ol, ow = il + 2 * wall, iw + 2 * wall
        top = h + b["pcb_t"] + b["top_h"]
        return ol * ow * 3 + (ol * ow - il * iw) * top + 4 * math.pi * ((b["standoff_d"] / 2) ** 2 - (tap / 2) ** 2) * h

    assert vol(c) == pytest.approx(expected(b["standoff_h"]))
    assert vol(c, "m1") == pytest.approx(expected(6))  # taller standoffs raise the walls too


def test_electronics_bay_esp32(cases):
    c = get(cases, "bay_esp32")
    assert_sound(c)
    b = load_card("electronics/esp32_devkit")["params_mm"]
    pits = b["pcb_t"] + b["pin_below"]
    assert c["floor_t"] == pytest.approx(pits + PROFILE["min_wall_mm"])  # floor sized by the pin pits
    assert c["probes"] == [False, True, False, True, False]  # pit, floor, USB cut, wall, board recess
    assert all(c["after_pin_below"]["gates"].values())
    assert c["after_pin_below"]["zmin"] == pytest.approx(-(pits + 1 + PROFILE["min_wall_mm"]))


def test_insert_and_counterbore(cases):
    c = get(cases, "insert_counterbore")
    assert_sound(c)
    ins = load_card("fastener/m3_insert")["params_mm"]
    scr = load_card("fastener/m3_screw")["params_mm"]
    clr, m3 = PROFILE["fits"]["clearance"], PROFILE["screws"]["M3"]["clearance"]
    a = lambda d: math.pi * (d / 2) ** 2
    bolt = a(m3) * 10 + (a(scr["head_d"] + clr) - a(m3)) * (scr["head_h"] + clr / 2)
    assert vol(c) == pytest.approx(40 * 40 * 10 - a(ins["hole_d"]) * ins["hole_depth"] - bolt)
    assert vol(c, "m1") == pytest.approx(40 * 40 * 10 - a(4.2) * ins["hole_depth"] - bolt)


def test_servo_u_bracket(cases):
    """Manipulator pack composite: horn plate + idler plate + bridge, one solid, follows card and own params."""
    c = get(cases, "u_bracket")
    assert_sound(c)
    p = load_card("servo/mg996r")["params_mm"]
    horn_z = p["spline_top"] + p["horn_top_above_tip"] - p["horn_disc_t"]
    L = round(math.hypot(p["flange_l"] / 2 - p["shaft_x"], p["body_w"] / 2) + 3, 2)
    t, w = 5, 30

    def bbox(gap, below):
        return [-w / 2, -w / 2, -(horn_z + below + gap) - t, L + t, w / 2, t]

    r = c["ret"]
    assert (r["length"], r["width"], r["thick"], r["gap"]) == pytest.approx((L, w, t, 2))
    assert r["plate_b_z"] == pytest.approx(-(horn_z + p["body_below"] + 2) - t)
    assert c["m0"]["bbox"] == pytest.approx(bbox(2, p["body_below"]))
    assert c["m1"]["bbox"] == pytest.approx(bbox(4, 30))       # gap and the card's case depth both drive plate B
    assert c["report"]["n_solids"] == 1
    assert c["probes"] == [False, False, True, True, True, False]
    assert c["report"]["params"]["ubracket_s_shoulder_length"]["source"].startswith("sdk")


# ---- SDK v1 (T2.1) ---------------------------------------------------------------------------

def test_polyline(cases):
    c = get(cases, "polyline")
    assert_sound(c)
    assert vol(c) == pytest.approx(30 * 20 / 2 * 5)
    assert vol(c, "m1") == pytest.approx(40 * 20 / 2 * 5)


def test_rect_rounded_corners(cases):
    c = get(cases, "rounded_rect")
    assert_sound(c)
    rr = lambda w, h, r: (w * h - (4 - math.pi) * r * r) * 2
    assert vol(c) == pytest.approx(rr(40, 20, 5) + rr(10, 10, 2))
    assert vol(c, "m1") == pytest.approx(rr(50, 20, 3) + rr(10, 10, 2))


def test_fillet_chamfer_on_cap_edges_survive_edge_split(cases):
    """Dress-ups go right after the pad they name, so a later notch crossing the edge cannot rename it (S13)."""
    c = get(cases, "fillet_chamfer")
    assert_sound(c)
    assert c["order"] == ["base_pad", "base_pad_fillet", "base_pad_chamfer", "notch_pocket"]
    assert vol(c, "m1") > 2 * vol(c)                  # resized, not a stale shape
    assert all(c["split"].values())
    # rounded top edges x3, chamfered bottom edge, sketch-rounded corner; core and side wall solid
    assert c["probes"] == [False, False, False, False, False, True, True]
    params = c["report"]["params"]
    assert {"base_pad_fillet_r", "base_pad_chamfer_size", "base_rect0_r"} <= set(params)


def test_hole_countersink_and_floor_fillet(cases):
    c = get(cases, "hole_edges")
    assert_sound(c)
    assert c["probes"] == [False, True, True]
    assert vol(c, "m1") < vol(c)                      # deeper, wider bore: dress-ups followed


def test_shell(cases):
    c = get(cases, "shell")
    assert_sound(c)
    assert vol(c) == pytest.approx(40 * 30 * 20 - 36 * 26 * 18)
    assert vol(c, "m1") == pytest.approx(40 * 30 * 20 - 34 * 24 * 17)


def test_mirror(cases):
    c = get(cases, "mirror")
    assert_sound(c)
    boss = math.pi * 9 * 5
    assert vol(c) == pytest.approx(80 * 40 * 10 + 2 * boss)
    assert c["probes"] == [True, True, False]         # mirror copy followed the original to x = 25


def test_pattern_linear_count_is_a_param(cases):
    c = get(cases, "pattern_linear")
    assert_sound(c)
    boss = math.pi * 9 * 5
    assert vol(c) == pytest.approx(80 * 40 * 10 + 3 * boss)
    assert vol(c, "m1") == pytest.approx(80 * 40 * 10 + 4 * boss)
    assert c["report"]["params"]["pattern0_count"]["unit"] == "-"
    assert c["probes"] == [True, True]


def test_pattern_polar(cases):
    c = get(cases, "pattern_polar")
    assert_sound(c)
    disc, boss = math.pi * 900 * 5, math.pi * 9 * 5
    assert vol(c) == pytest.approx(disc + 6 * boss)
    assert vol(c, "m1") == pytest.approx(disc + 3 * boss)


def test_boss_with_insert_hole(cases):
    c = get(cases, "boss")
    assert_sound(c)
    ins = load_card("fastener/m3_insert")["params_mm"]
    a = lambda d: math.pi * (d / 2) ** 2
    hole = 2 * a(ins["hole_d"]) * ins["hole_depth"]
    assert vol(c) == pytest.approx(40 * 40 * 10 + 2 * a(8) * 6 - hole)   # the pedestal sits inside the plate
    assert vol(c, "m1") == pytest.approx(40 * 40 * 10 + 2 * a(8) * 8 - hole)
    assert c["probes"] == [False, True, True]


def test_rib(cases):
    c = get(cases, "rib")
    assert_sound(c)
    assert vol(c) == pytest.approx(40 * 20 * 5 + 15 * 15 / 2 * 3)
    assert vol(c, "m1") == pytest.approx(40 * 20 * 5 + 15 * 15 / 2 * 4)


def test_v1_errors(cases):
    c = get(cases, "errors")
    assert "pad or pocket" in c["fillet_not_extrusion"]["cause"]
    assert "use start, end or caps" in c["fillet_class"]["cause"]
    assert "rounded in the sketch" in c["fillet_side"]["cause"] and "r=" in c["fillet_side"]["action"]
    assert "rounded in the sketch" not in c["shell_side"]["cause"]
    assert "< half the smaller side" in c["rect_radius"]["cause"]
    assert "int >= 2" in c["pattern_count"]["cause"]
    assert "not a feature" in c["not_a_feature"]["cause"]
    assert "coincide" in c["polyline_repeat"]["cause"]


# ---- manipulator pack composites (T2.1) --------------------------------------------------------

def _a(d):
    return math.pi * (d / 2) ** 2


def test_bearing_seat_press_fit_and_lip(cases):
    c = get(cases, "bearing_seat")
    assert_sound(c)
    b = load_card("bearing/608zz")["params_mm"]
    od = b["outer_d"] + PROFILE["fits"]["press"]
    assert c["ret"] == pytest.approx({"outer_d": od, "depth": b["width"], "lip_d": b["housing_shoulder_d"],
                                      "bore_d": b["bore_d"]})
    seat = _a(od) * b["width"]
    assert vol(c) == pytest.approx(40 * 40 * 10 - seat - _a(b["housing_shoulder_d"]) * 3)
    assert vol(c, "m1") == pytest.approx(40 * 40 * 12 - seat - _a(b["housing_shoulder_d"]) * 5)  # seat followed its connector
    assert c["report"]["params"]["bearing_608zz_outer_d"]["source"] == "card:bearing/608zz"   # digit-led id -> family prefix


def test_horn_coupler_hub(cases):
    c = get(cases, "horn_coupler")
    assert_sound(c)
    p = load_card("servo/mg996r")["params_mm"]
    clr, tap, min_wall = PROFILE["fits"]["clearance"], PROFILE["screws"]["M2"]["tap"], PROFILE["min_wall_mm"]
    ceiling, screw = p["horn_disc_t"] + clr / 2, 2.5 * 2
    thick = ceiling + screw + min_wall
    assert c["ret"] == pytest.approx({"hub_d": p["horn_disc_d"] + clr + 6, "thick": thick})

    def expected(wall):
        hub = p["horn_disc_d"] + clr + 2 * wall
        return (_a(hub) * thick - _a(p["horn_disc_d"] + clr) * ceiling - 4 * _a(tap) * screw
                - _a(p["horn_access_d"]) * (thick - ceiling))

    assert vol(c) == pytest.approx(expected(3)) and vol(c, "m1") == pytest.approx(expected(4))
    assert "s_elbow_horn_screw_depth_2" not in c["report"]["params"]   # one shared screw-depth alias


def test_link_bar(cases):
    c = get(cases, "link_bar")
    assert_sound(c)
    bar = lambda L: (L * 20 + _a(20)) * 6 - 2 * _a(PROFILE["screws"]["M3"]["clearance"]) * 6
    assert vol(c) == pytest.approx(bar(80)) and vol(c, "m1") == pytest.approx(bar(100))


def test_hollow_link_trough(cases):
    c = get(cases, "hollow_link")
    assert_sound(c)
    link = lambda L: (L * 20 + _a(20)) * 8 - ((L - 40) * 16 + _a(16)) * 6
    assert vol(c) == pytest.approx(link(100)) and vol(c, "m1") == pytest.approx(link(120))
    assert c["probes"] == [False, True, True, True]


def test_turntable_base(cases):
    """Yaw base: servo well + pocket on the base axis, rollers level with the horn face carry the turret."""
    c = get(cases, "turntable")
    assert_sound(c)
    p, b = load_card("servo/mg996r")["params_mm"], load_card("bearing/623zz")["params_mm"]
    clr = PROFILE["fits"]["clearance"]
    r = c["ret"]
    horn_z = p["spline_top"] + p["horn_top_above_tip"] - p["horn_disc_t"]
    assert r["horn_z"] == pytest.approx(horn_z) and r["axis_x"] == pytest.approx(p["shaft_x"])
    assert r["axle_z"] + b["outer_d"] / 2 == pytest.approx(horn_z)      # roller tops carry the turret at the horn face
    assert r["top_z"] == pytest.approx(horn_z - 1)
    reach = math.hypot(p["flange_l"] / 2 + p["shaft_x"], p["body_w"] / 2)
    assert r["roller_r"] - b["width"] / 2 - clr / 2 > reach + 2          # rollers clear the flange sweep
    assert 90 <= r["base_d"] <= 120                                      # knowledge: base 90-120 mm
    R, ax = r["base_d"] / 2, r["axis_x"]
    assert c["m0"]["bbox"] == pytest.approx([ax - R, -R, r["bottom_z"], ax + R, R, r["top_z"]])
    assert c["m1"]["bbox"][5] == pytest.approx(horn_z - 2)              # protrude 1 -> 2 lowers the ring
    # roller pockets 0 and 1, axle hole, ring between rollers, flange well, case pocket, floor
    assert c["probes"] == [False, False, False, True, False, False, True]


def test_pack_composite_errors(cases):
    c = get(cases, "errors")
    assert "not a bearing" in c["seat_not_bearing"]["cause"]
    assert "rollers must be an int >= 3" in c["turntable_rollers"]["cause"]
    assert "no room for the trough" in c["hollow_no_room"]["cause"]


V1_SAMPLES = ["polyline", "rounded_rect", "fillet_chamfer", "hole_edges", "shell", "mirror", "pattern_linear",
              "pattern_polar", "boss", "rib", "bearing_seat", "horn_coupler", "link_bar", "hollow_link", "turntable",
              "u_bracket"]


@pytest.mark.parametrize("name", V1_SAMPLES)
def test_v1_sample_passes_flex(cases, name):
    """P2 §3.6: every SDK v1 call flexes on a sample (its own literal dimensions +-20 %, capped at 12 runs)."""
    f = get(cases, name)["flex"]
    assert f["runs"] > 0 and f["restored"], f
    assert f["failures"] == [], f["failures"]
