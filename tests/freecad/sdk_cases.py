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

OUT = request["out"]
CASES = {}


def case(fn):
    CASES[fn.__name__] = fn
    return fn


def measure(part):
    sh = part.body.Shape
    bb = sh.BoundBox
    return {"volume": sh.Volume, "bbox": [bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax]}


def run(part, changes):
    """report + measure, apply {alias: value}, recompute, report + measure again."""
    before = part.report()
    m0 = measure(part)
    for alias, v in changes.items():
        part.set_param(alias, v)
    after = part.report()
    out = {"report": before, "m0": m0, "m1": measure(part), "after_gates": after["gates"],
           "after_sketches": after["sketches"], "after_connectors": after["connectors"]}
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
        out.update(run(part, {"ubracket_s_shoulder_gap": 4, "s_shoulder_body_below": 30}))
        part.set_param("ubracket_s_shoulder_gap", 2)
        part.set_param("s_shoulder_body_below", 26)
        part.doc.recompute()
        out["probes"] = inside(part, *probes)
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
    }
    out = {}
    for k, fn in attempts.items():
        try:
            fn()
            out[k] = None
        except SDKError as e:
            out[k] = e.signal
    return out


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
