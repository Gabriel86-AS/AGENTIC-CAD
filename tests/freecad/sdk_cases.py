"""SDK v0 cases, run inside freecadcmd by tests/test_sdk.py (one worker run for all cases).

Each case builds a small part, records report() and the measured geometry, changes a Params cell,
recomputes and measures again (proves expression binding). result[case] = dict or {"error": tb}.
"""
import math
import os
import traceback

import FreeCAD as App

import sdk
from sdk import Part, PlanParams, SDKError
from validate.flex import flex

OUT = request["out"]
FLEX_RUNS = 12   # L4 on every SDK v1 sample (P2 §3.6): 4 dimensions x min / max / interior
CASES = {}


def case(fn):
    CASES[fn.__name__] = fn
    return fn


def measure(part):
    sh = part.body.Shape
    bb = sh.BoundBox
    return {"volume": sh.Volume, "bbox": [bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax]}


def run(part, changes, flex_runs=0):
    """report + measure, apply {alias: value}, recompute, report + measure again; flex_runs > 0: L4 at nominal
    first (literals too, so SDK calls are flexed over their own dimensions)."""
    before = part.report()
    m0 = measure(part)
    out = {}
    if flex_runs:
        facts, sigs = flex(part, part.name, part.name, max_runs=flex_runs, sources=("part", "literal"))
        out["flex"] = dict(facts, signals=sigs)
    for alias, v in changes.items():
        part.set_param(alias, v)
    after = part.report()
    out.update({"report": before, "m0": m0, "m1": measure(part), "after_gates": after["gates"],
                "after_sketches": after["sketches"], "after_connectors": after["connectors"]})
    return out


def plate(name, w=40, d=40, t=10, **kw):
    part = Part(name)
    p = part.params(plate_w=w, plate_d=d, thick=t, **kw)
    part.pad(part.sketch("base", on="XY").rect(p.plate_w, p.plate_d), p.thick)
    return part, p


@case
def params():
    sdk.configure()
    P = PlanParams({"plan_w": {"value": 60, "min": 40, "max": 80, "unit": "mm"}})
    part = Part("params_case")
    p = part.params(width=P.plan_w, thick=5, depth={"value": 30, "min": 20, "max": 40}, plan_w=P.plan_w)
    part.pad(part.sketch("base", on="XY").rect(p.width, p.depth), p.thick)
    out = run(part, {"plan_w": 70})
    out["self_alias_expr"] = p.plan_w.expr
    return out


@case
def datum():
    part, p = plate("datum_case")
    top = part.datum("top", plane="XY", offset=p.thick)
    part.pad(part.sketch("boss", on=top).circle(10), 4)
    side = part.datum("side", plane="XZ", offset=20)
    part.pad(part.sketch("fin", on=side).rect(6, 6, center=(0, 5)), 2)
    tilt = part.datum("tilt", origin=(20, 0, 5), z=(1, 0, 0), x=(0, 1, 0))
    part.pad(part.sketch("knob", on=tilt).circle(4), 3)
    out = run(part, {"thick": 12, "side_offset": 19, "tilt_o_x": 19})
    part.set_param("tilt_yaw", 180)
    part.doc.recompute()
    out["tilt_z"] = list(part.datums["tilt"].obj.Placement.Rotation.multVec(App.Vector(0, 0, 1)))
    out["tilt_angles"] = {k: v for k, v in part.params_info.items() if k.startswith("tilt_") and v["unit"] == "deg"}
    return out


@case
def rect():
    part = Part("rect_case")
    part.pad(part.sketch("sk", on="XY").rect(20, 10, center=(10, 5)), 2)
    return run(part, {"sk_rect0_w": 24, "sk_rect0_cx": 12})


@case
def circle():
    part = Part("circle_case")
    part.pad(part.sketch("sk", on="XY").circle(10, center=(5, 0)), 2)
    return run(part, {"sk_circle0_d": 12})


@case
def slot():
    part = Part("slot_case")
    part.pad(part.sketch("sk", on="XY").slot((0, 0), (30, 10), 8), 3)
    return run(part, {"sk_slot0_width": 10, "sk_slot0_ex": 40})


@case
def polygon():
    part = Part("polygon_case")
    part.pad(part.sketch("sk", on="XY").polygon(6, 20), 2)
    return run(part, {"sk_polygon0_d": 30})


@case
def pad_pocket():
    part, p = plate("pocket_case")
    top = part.datum("top", plane="XY", offset=p.thick)
    part.pocket(part.sketch("recess", on=top).rect(10, 10), depth=4)
    part.pocket(part.sketch("bore", on="XY").circle(6, center=(12, 12)), through=True, symmetric=True)
    part.pad(part.sketch("rib", on="XZ").rect(4, 6, center=(0, -3)), 2, symmetric=True)
    return run(part, {"recess_pocket_depth": 5})


@case
def hole():
    part, p = plate("hole_case")
    top = part.datum("top", plane="XY", offset=p.thick)
    part.hole(top, at=[(10, 10), (-10, -10)], d=3, fit="clearance", name="pair")
    part.hole(top, at=(10, -10), fit="tap_m3", depth=6, name="tapped")
    part.hole("XZ", at=(0, 5), d=3, fit="clearance", horizontal=True, name="cross")
    return run(part, {"fit_clearance": 0.4})


@case
def connector():
    part, p = plate("connector_case", length=40)
    part.connector("tip", origin=(p.length, 0, p.thick), z=(0, 1, 0), x=(1, 0, 0), label="tool")
    part.connector("bottom", origin=(0, 0, 0), z=(0, 0, -1), x=(1, 0, 0), label="mount")
    part.connector("side", origin=(0, 20, 5), rot=(90, 0, 0), label="cable")
    return run(part, {"length": 55, "thick": 8, "tip_roll": -45, "side_yaw": 180})


def servo_block(name):
    part, p = plate(name, w=80, d=40, t=40)
    part.connector("servo_mount", origin=(0, 0, p.thick), z=(0, 0, 1), x=(1, 0, 0), label="servo_mount")
    return part, p


@case
def mount():
    part, _ = servo_block("mount_case")
    part.mount("servo/mg996r", at="servo_mount")
    return run(part, {"mg996r_body_l": 42.7})


@case
def mount_plan_component():
    sdk.configure(components={"s_yaw": "servo/mg996r"})
    try:
        part, _ = servo_block("mount_plan_case")
        part.mount("s_yaw", at="servo_mount")
        return run(part, {"thick": 45})
    finally:
        sdk.configure()


@case
def mount_rotated():
    """Servo seated on a side connector; turning the connector by its Params turns every cut."""
    part, p = plate("mount_rot_case", w=80, d=80, t=40, mx=40, my=0)
    part.connector("side_mount", origin=(p.mx, p.my, 20), z=(1, 0, 0), x=(0, 1, 0), label="servo_mount")
    part.mount("servo/mg996r", at="side_mount")
    return run(part, {"side_mount_yaw": 180, "mx": 0, "my": 40})


@case
def cable_channel():
    part = Part("channel_case")
    part.pad(part.sketch("sk", on="XY").rect(100, 20), 20)
    part.cable_channel((-40, 0, 10), (40, 0, 10), 6)
    out = run(part, {"channel0_start_x": -30})
    part.set_param("channel0_start_x", -40)
    part.set_param("channel0_end_z", 14)  # turns the channel: direction follows the end points
    out["tilted_gates"] = part.report()["gates"]
    out["m2"] = measure(part)
    return out


@case
def save_reopen():
    part, _ = servo_block("reopen_case")
    part.mount("servo/mg996r", at="servo_mount")
    path = os.path.join(OUT, "reopen_case.FCStd")
    part.save(path)
    v0 = part.body.Shape.Volume
    part.close()
    doc = App.openDocument(path)
    sheet = doc.getObject("Params")
    sheet.set(sheet.getCellFromAlias("thick"), "=50 mm")
    doc.recompute()
    out = {"v0": v0, "v1": doc.getObject("Body").Shape.Volume,
           "invalid": [o.Name for o in doc.Objects if "Invalid" in o.State or "Error" in o.State],
           "lcs_z": doc.getObject("servo_mount").Placement.Base.z,
           "n_expr": sum(len(o.ExpressionEngine) for o in doc.Objects if hasattr(o, "ExpressionEngine"))}
    App.closeDocument(doc.Name)
    return out


def inside(part, *pts):
    """Is each part-frame point inside the body solid?"""
    sh = part.body.Shape
    return [sh.isInside(App.Vector(*p), 1e-6, True) for p in pts]


@case
def mate_to():
    """Horn-driven plate: disc recess, M2 tap holes (into the part) and screwdriver access on the axis."""
    sdk.configure(components={"s_shoulder": "servo/mg996r"})
    try:
        part, p = plate("mate_case", w=60, d=40, t=10)
        part.connector("shoulder_horn", origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="servo_horn_face")
        part.mate_to("s_shoulder.horn", at="shoulder_horn")
        return run(part, {"s_shoulder_horn_disc_t": 3})
    finally:
        sdk.configure()


@case
def bay_generic():
    part = Part("bay_generic_case")
    part.connector("bay", origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="ebay_mount")
    part.electronics_bay("electronics/generic_bay", at="bay")
    out = run(part, {"bay_generic_bay_margin": 3, "m3_screw_nut_m": 3.0})
    out["floor_t"] = {"before": out["report"]["params"]["bay_generic_bay_floor_t"]["current"],
                      "after": part.report()["params"]["bay_generic_bay_floor_t"]["current"]}
    return out


@case
def bay_pca9685():
    part = Part("bay_pca_case")
    part.connector("bay", origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="board_mount")
    part.electronics_bay("electronics/pca9685", at="bay")
    return run(part, {"pca9685_standoff_h": 6})


@case
def bay_esp32():
    """ESP32 recess + pin pits + USB plug cut through the -x wall; the floor grows with the pits."""
    part = Part("bay_esp32_case")
    part.connector("bay", origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="board_mount")
    part.electronics_bay("electronics/esp32_devkit", at="bay")
    probes = [(0, 12.7, -5), (0, 0, -5), (-30.4, 0, 2), (-30.4, 10, 2), (0, 0, -1)]
    # pin pit, floor between pits, USB cut in the -x wall, -x wall beside it, board recess
    out = run(part, {})
    out["probes"] = inside(part, *probes)
    out["floor_t"] = out["report"]["params"]["bay_esp32_devkit_floor_t"]["current"]
    part.set_param("esp32_devkit_pin_below", 10)
    out["after_pin_below"] = {"gates": part.report()["gates"], "zmin": measure(part)["bbox"][2]}
    return out


@case
def insert_counterbore():
    part, p = plate("fastener_case")
    top = part.datum("top", plane="XY", offset=p.thick)
    part.hole(top, at=(10, 10), fit="insert_m3", name="insert")
    part.hole(top, at=(-10, -10), fit="clearance_m3", counterbore=True, name="bolt")
    return run(part, {"m3_insert_hole_d": 4.2})


@case
def u_bracket():
    """Pack composite: U bracket on the shoulder horn, idler plate under the case, bridge beyond the flange."""
    sdk.configure(components={"s_shoulder": "servo/mg996r"})
    try:
        part = Part("ubracket_case")
        part.connector("shoulder_horn", origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="servo_horn_face")
        u = part.servo_u_bracket("s_shoulder", at="shoulder_horn")
        out = {"ret": {k: float(getattr(u, k)) for k in ("length", "width", "thick", "gap", "bridge_x", "plate_b_z", "mid_z")}}
        # idler bore (axis, plate B), access hole (axis, plate A), plate B off axis, bridge, plate A bar, servo space
        probes = [(0, 0, u.plate_b_z.value + 1), (0, 0, 4), (8, 0, u.plate_b_z.value + 1),
                  (u.length.value + 2, 0, -20), (u.length.value - 2, 5, 4), (0, 0, -20)]
        out.update(run(part, {"ubracket_s_shoulder_gap": 4, "s_shoulder_body_below": 30}, flex_runs=FLEX_RUNS))
        part.set_param("ubracket_s_shoulder_gap", 2)
        part.set_param("s_shoulder_body_below", 26)
        part.doc.recompute()
        out["probes"] = inside(part, *probes)
        return out
    finally:
        sdk.configure()


# ---- SDK v1 (T2.1) ---------------------------------------------------------------------------

@case
def polyline():
    part = Part("polyline_case")
    part.pad(part.sketch("sk", on="XY").polyline([(0, 0), (30, 0), (0, 20)]), 5)
    return run(part, {"sk_poly0_p1_x": 40}, flex_runs=FLEX_RUNS)


@case
def rounded_rect():
    part = Part("rrect_case")
    p = part.params(base_t=2)
    part.pad(part.sketch("sk", on="XY").rect(40, 20, r=5), p.base_t)
    top = part.datum("top", plane="XY", offset=p.base_t)
    part.pad(part.sketch("off", on=top).rect(10, 10, center=(10, 0), r=2), 2)   # off-centre, stacked
    return run(part, {"sk_rect0_r": 3, "sk_rect0_w": 50}, flex_runs=FLEX_RUNS)


@case
def fillet_chamfer():
    """S13 geometry: rounded-corner plate, top fillet + bottom chamfer on its pad, a notch pocket made
    before them. Moving the notch across the +x top edge splits it: the dress-ups sit before the notch."""
    part = Part("dress_case")
    p = part.params(plate_w=40, plate_d=20, thick=10, notch_x=5)
    pad = part.pad(part.sketch("base", on="XY").rect(p.plate_w, p.plate_d, r=4), p.thick)
    top = part.datum("top", plane="XY", offset=p.thick)
    part.pocket(part.sketch("notch", on=top).rect(4, 6, center=(p.notch_x, 0)), depth=3)
    part.fillet(pad, 2, edges="end")
    part.chamfer("base_pad", 1, edges="start")
    out = run(part, {"plate_w": 60, "thick": 15}, flex_runs=FLEX_RUNS)
    out["order"] = [o.Name for o in part.body.Group if o.isDerivedFrom("PartDesign::Feature")]
    part.set_param("notch_x", 28)                                # notch 26..30: crosses the +x top edge
    out["split"] = part.report()["gates"]
    out["probes"] = inside(part, (29.8, 6, 14.8), (-29.8, 6, 14.8), (10, 9.8, 14.8),   # rounded top edges
                           (29.8, 6, 0.2), (29.2, 9.2, 7), (10, 0, 7), (29.8, 6, 7))  # chamfered bottom, corner r 4, core, side
    return out


@case
def hole_edges():
    """Countersink (chamfer of a blind pocket's rim) and a floor fillet (its end edges)."""
    part, p = plate("hole_edges_case", w=40, d=40, t=10)
    top = part.datum("top", plane="XY", offset=p.thick)
    bore = part.pocket(part.sketch("bore", on=top).circle(6, center=(10, 10)), depth=6)
    part.chamfer(bore, 1, edges="start", name="countersink")
    part.fillet(bore, 1, edges="end", name="floor_round")
    part.doc.recompute()
    probes = inside(part, (13.4, 10, 9.6), (12.85, 10, 4.15), (14.5, 10, 5))  # countersunk, floor fillet, wall
    out = run(part, {"bore_pocket_depth": 7, "bore_circle0_d": 7}, flex_runs=FLEX_RUNS)
    out["probes"] = probes
    return out


@case
def shell():
    part, p = plate("shell_case", w=40, d=30, t=20)
    part.shell("base_pad", 2, open="end")
    return run(part, {"base_pad_shell_wall": 3}, flex_runs=FLEX_RUNS)


def _boss_plate(name, w=80, d=40, at=(20, 10)):
    part, p = plate(name, w=w, d=d, t=10)
    top = part.datum("top", plane="XY", offset=p.thick)
    boss = part.pad(part.sketch("bossk", on=top).circle(6, center=at), 5)
    return part, boss


@case
def mirror():
    part, boss = _boss_plate("mirror_case")
    part.mirror(boss, plane="YZ")
    out = run(part, {"bossk_circle0_cx": 25}, flex_runs=FLEX_RUNS)
    out["probes"] = inside(part, (-25, 10, 12), (25, 10, 12), (-20, 10, 12))
    return out


@case
def pattern_linear():
    part, boss = _boss_plate("linear_case", at=(-30, 0))
    part.pattern_linear(boss, "x", spacing=20, count=3)
    out = run(part, {"pattern0_count": 4}, flex_runs=FLEX_RUNS)
    out["probes"] = inside(part, (30, 0, 12), (10, 0, 12))
    return out


@case
def pattern_polar():
    part = Part("polar_case")
    p = part.params(disc_t=5)
    part.pad(part.sketch("disc", on="XY").circle(60), p.disc_t)
    top = part.datum("top", plane="XY", offset=p.disc_t)
    boss = part.pad(part.sketch("bossk", on=top).circle(6, center=(20, 0)), 5)
    part.pattern_polar(boss, "z", count=6)
    return run(part, {"pattern0_count": 3}, flex_runs=FLEX_RUNS)


@case
def boss():
    part, p = plate("boss_case", w=40, d=40, t=10)
    top = part.datum("top", plane="XY", offset=p.thick)
    part.boss(top, at=[(10, 10), (-10, -10)], d=8, height=6, hole="insert_m3")
    part.boss("XY", at=(0, 0), d=10, height=3, name="pedestal")   # on an origin plane: grows +z inside the plate
    out = run(part, {"boss0_height": 8}, flex_runs=FLEX_RUNS)
    out["probes"] = inside(part, (10, 10, 17), (13.5, 10, 17), (10, 10, 9.5))  # hole, boss wall, below the hole
    return out


@case
def rib():
    part, p = plate("rib_case", w=40, d=20, t=5)
    part.rib("XZ", [(0, p.thick), (15, p.thick), (0, p.thick + 15)], thick=3)
    return run(part, {"rib0_thick": 4}, flex_runs=FLEX_RUNS)


# ---- manipulator pack composites (T2.1) --------------------------------------------------------

@case
def bearing_seat():
    part, p = plate("seat_case", w=40, d=40, t=10)
    part.connector("seat", origin=(0, 0, 3), z=(0, 0, 1), x=(1, 0, 0), label="bearing_seat")
    s = part.bearing_seat("bearing/608zz", at="seat")
    out = {"ret": {k: float(getattr(s, k)) for k in ("outer_d", "depth", "lip_d", "bore_d")}}
    out.update(run(part, {"seat_o_z": 5, "thick": 12}, flex_runs=FLEX_RUNS))
    return out


@case
def horn_coupler():
    sdk.configure(components={"s_elbow": "servo/mg996r"})
    try:
        part = Part("coupler_case")
        part.connector("elbow_horn", origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="servo_horn_face")
        c = part.horn_coupler("s_elbow", at="elbow_horn")
        out = {"ret": {k: float(getattr(c, k)) for k in ("hub_d", "thick")}}
        out.update(run(part, {"coupler_s_elbow_wall": 4}, flex_runs=FLEX_RUNS))
        return out
    finally:
        sdk.configure()


@case
def link_bar():
    part = Part("link_bar_case")
    part.link_bar("XY", 80, 20, 6, end_holes="clearance_m3")
    return run(part, {"link0_length": 100}, flex_runs=FLEX_RUNS)


@case
def hollow_link():
    part = Part("hollow_link_case")
    part.hollow_link("XY", 100, 20, 8, wall=2)
    out = run(part, {"link0_length": 120}, flex_runs=FLEX_RUNS)
    out["probes"] = inside(part, (60, 0, 7), (60, 0, 1), (10, 0, 7), (60, 9, 7))  # trough, floor, solid end, side wall
    return out


@case
def turntable():
    sdk.configure(components={"s_yaw": "servo/mg996r"})
    try:
        part = Part("turntable_case")
        part.connector("yaw_mount", origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="servo_mount")
        t = part.turntable_base("s_yaw", at="yaw_mount")
        r = {k: float(getattr(t, k)) for k in ("base_d", "roller_r", "top_z", "horn_z", "axle_z", "bottom_z", "axis_x")}
        ax, rr, top = r["axis_x"], r["roller_r"], r["top_z"]
        c60, s60 = math.cos(math.radians(60)), math.sin(math.radians(60))
        part.doc.recompute()
        out = {"ret": r, "probes": inside(
            part, (ax + rr, 0, top - 0.5), (ax - rr * c60, rr * s60, top - 0.5),         # roller pockets 0 and 1
            (ax + rr + 2 + 1.5, 0, r["axle_z"]),                                        # axle hole beyond pocket 0
            (ax + rr * c60, rr * s60, top - 0.5),                                       # ring between rollers
            (0, 0, 1), (0, 0, -10), (0, 0, r["bottom_z"] + 1))}                         # well, case pocket, floor
        out.update(run(part, {"turntable_s_yaw_protrude": 2}, flex_runs=FLEX_RUNS))
        return out
    finally:
        sdk.configure()


PROGRAM = '''
import math
from agenticcad import Part

def build(P):
    part = Part("link_program", material="PLA")
    p = part.params(length=P.link_len, width=20, thick=6)
    s = part.sketch("profile", on="XY").slot((0, 0), (p.length, 0), p.width)
    part.pad(s, p.thick)
    part.hole("XY", at=[(0, 0), (p.length, 0)], d=5, fit="press", name="pins")
    part.connector("tip", origin=(p.length, 0, p.thick), z=(0, 0, 1), x=(1, 0, 0), label="tool")
    return part
'''


@case
def program():
    """A part program in the agent's form: imports only `agenticcad`, build(P) returns the Part."""
    ns = {}
    exec(compile(PROGRAM, "parts/link_program.py", "exec"), ns)
    part = ns["build"](PlanParams({"link_len": {"value": 80, "min": 60, "max": 120}}))
    out = run(part, {"link_len": 100})
    out["material"] = part.material
    return out


def _plan_redefined():
    part = Part("err_o")
    part.params(span=PlanParams({"plate_len": {"value": 50, "min": 40, "max": 60}}).plate_len)
    part.params(plate_len=60)


@case
def errors():
    attempts = {
        "zero_width": lambda: Part("err_a").sketch("sk").rect(0, 5),
        "unit_alias": lambda: Part("err_b").params(h=5),
        "plan_redefined": lambda: _plan_redefined(),
        "bad_plane": lambda: Part("err_c").sketch("sk", on="top_face"),
        "pocket_args": lambda: _pocket_both(),
        "unknown_fit": lambda: Part("err_e").hole("XY", at=(0, 0), d=3, fit="loose"),
        "slot_degenerate": lambda: Part("err_f").sketch("sk").slot((1, 1), (1, 1), 4),
        "not_orthogonal": lambda: Part("err_g").connector("conn", (0, 0, 0), z=(0, 0, 1), x=(0, 1, 1), label="x"),
        "duplicate_name": lambda: _dup(),
        "unknown_component": lambda: _mount_unknown(),
        "slotted_no_host": lambda: _with_conn("err_j").mount("electronics/generic_bay", at="conn"),
        "mate_no_horn": lambda: _with_conn("err_k").mate_to("servo/mg996r.output_shaft", at="conn"),
        "counterbore_tap": lambda: Part("err_l").hole("XY", at=(0, 0), fit="tap_m3", counterbore=True),
        "bay_not_electronics": lambda: _with_conn("err_m").electronics_bay("servo/mg996r", at="conn"),
        "u_bracket_not_servo": lambda: _with_conn("err_n").servo_u_bracket("electronics/pca9685", at="conn"),
        "fillet_not_extrusion": lambda: _fillet_mirror(),
        "fillet_class": lambda: _err_plate("err_q").fillet("base_pad", 1, edges="top"),
        "fillet_side": lambda: _err_plate("err_v").fillet("base_pad", 1, edges="side"),
        "rect_radius": lambda: Part("err_w").sketch("sk").rect(10, 6, r=3),
        "shell_side": lambda: _err_plate("err_r").shell("base_pad", 1, open="side"),
        "pattern_count": lambda: _err_plate("err_s").pattern_linear("base_pad", "x", 10, 1),
        "not_a_feature": lambda: _err_plate("err_t").fillet("nope", 1),
        "polyline_repeat": lambda: Part("err_u").sketch("sk").polyline([(0, 0), (5, 0), (5, 0), (0, 5)]),
        "seat_not_bearing": lambda: _with_conn("err_x").bearing_seat("servo/mg996r", at="conn"),
        "turntable_rollers": lambda: _with_conn("err_y").turntable_base("servo/mg996r", at="conn", rollers=2),
        "hollow_no_room": lambda: Part("err_z").hollow_link("XY", 30, 20, 6),
    }
    out = {}
    for k, fn in attempts.items():
        try:
            fn()
            out[k] = None
        except SDKError as e:
            out[k] = e.signal
    return out


def _err_plate(name):
    return plate(name)[0]


def _fillet_mirror():
    part, boss = _boss_plate("err_p")
    part.fillet(part.mirror(boss), 1)


def _pocket_both():
    part = Part("err_d")
    part.pocket(part.sketch("sk").circle(3), depth=2, through=True)


def _dup():
    part = Part("err_h")
    part.sketch("sk")
    part.sketch("sk")


def _with_conn(name):
    part = Part(name)
    part.connector("conn", (0, 0, 0), (0, 0, 1), (1, 0, 0), "mount")
    return part


def _mount_unknown():
    part = Part("err_i")
    part.connector("mnt", (0, 0, 0), (0, 0, 1), (1, 0, 0), "servo_mount")
    part.mount("s_missing", at="mnt")


os.makedirs(OUT, exist_ok=True)
for name, fn in CASES.items():
    try:
        result[name] = fn()
    except Exception as e:
        result[name] = {"error": "%s: %s" % (type(e).__name__, e), "signal": getattr(e, "signal", None),
                        "traceback": traceback.format_exc()}
