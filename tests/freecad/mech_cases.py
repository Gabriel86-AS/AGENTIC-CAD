"""Mechanisms pack cases (T2.8), run inside freecadcmd by tests/test_mechanisms.py (one worker run).

Each two-sided composite is built as a pair of parts sharing the connector frame (as the plan places them
at rest); the case records both reports, L4 flex on each half, probe points, and the common volume of the
pair at rest and moved along / about the joint. result[case] = dict or {"error": tb}.
"""
import math
import traceback

import FreeCAD as App

import sdk
from sdk import Part, SDKError
from validate.flex import flex

FLEX_RUNS = 30   # L4 cap: every block and composite dimension of a sample
CASES = {}


def case(fn):
    CASES[fn.__name__] = fn
    return fn


def measure(part):
    sh = part.body.Shape
    bb = sh.BoundBox
    return {"volume": sh.Volume, "bbox": [bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax]}


def run(part, changes=None, flex_runs=FLEX_RUNS):
    before = part.report()
    m0 = measure(part)
    out = {}
    if flex_runs:
        facts, sigs = flex(part, part.name, part.name, max_runs=flex_runs, sources=("part", "literal"))
        out["flex"] = dict(facts, signals=sigs)
    for alias, v in (changes or {}).items():
        part.set_param(alias, v)
    after = part.report()
    out.update({"report": before, "m0": m0, "m1": measure(part), "after_gates": after["gates"],
                "after_sketches": after["sketches"]})
    for alias in changes or {}:      # back to nominal for the pair checks
        part.set_param(alias, part.params_info[alias]["value"])
    part.doc.recompute()
    return out


def inside(part, *pts):
    sh = part.body.Shape
    return [sh.isInside(App.Vector(*p), 1e-6, True) for p in pts]


def block(part, name, x, y, z):
    """Box x0..x1, y0..y1, z0..z1 (pads a rect on a datum at z0)."""
    d = part.datum(name + "_base", plane="XY", offset=z[0]) if z[0] else "XY"
    s = part.sketch(name, on=d).rect(x[1] - x[0], y[1] - y[0], center=((x[0] + x[1]) / 2, (y[0] + y[1]) / 2))
    part.pad(s, z[1] - z[0], name=name + "_pad")
    part.doc.recompute()


def common(a, b, place_b=None):
    a.doc.recompute()
    b.doc.recompute()
    sb = b.body.Shape.copy()
    if place_b is not None:
        sb.Placement = place_b
    return a.body.Shape.common(sb).Volume


def rot_z(deg):
    return App.Placement(App.Vector(), App.Rotation(App.Vector(0, 0, 1), deg))


def cyl_diams(part):
    return sorted({round(2 * f.Surface.Radius, 3) for f in part.body.Shape.Faces if "Cylinder" in str(f.Surface)})


def hinge_part(name, side, leaf_x, web_angle, **kw):
    part = Part(name)
    block(part, name + "_leaf", leaf_x, (-2, 2), (0, 30))
    part.connector("hinge", origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="hinge")
    h = part.hinge("hinge", side, 30, knuckles=3, web=8, web_angle=web_angle, **kw)
    return part, h


@case
def hinge_pair():
    a, ha = hinge_part("leaf_a", "a", (-25, -8), 180)
    b, hb = hinge_part("leaf_b", "b", (8, 25), 0)
    out = {"ret": {k: float(getattr(ha, k)) for k in ("length", "od", "gap", "seg")},
           "a": run(a, {"hinge_a_od": 9}), "b": run(b, {"hinge_b_gap": 1.0})}
    out["common"] = {deg: common(a, b, rot_z(deg)) for deg in (0, 45, 90, -90)}
    out["probes_a"] = inside(a, (0, 3, 5), (0, 3, 15), (0, 0, 5), (0, 3, 25), (-10, 0, 15))
    out["probes_b"] = inside(b, (0, 3, 5), (0, 3, 15), (0, 0, 15), (10, 0, 5))
    out["holes_a"], out["holes_b"] = cyl_diams(a), cyl_diams(b)
    return out


@case
def hinge_relief():
    part = Part("hinge_relief")
    block(part, "plate", (-25, 25), (-2, 2), (0, 30))
    part.connector("hinge", origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="hinge")
    part.hinge("hinge", "a", 30, knuckles=3, pin="filament", relief=True)
    out = run(part)
    out["probes"] = inside(part, (0, 1.8, 15), (3.3, 0, 15), (6, 0, 15), (0, 2.5, 5), (0, 0, 5))
    out["holes"] = cyl_diams(part)
    return out


def slide_parts(flare=2):
    a = Part("slide_a_%d" % flare)
    block(a, "carriage", (-15, 15), (-10, 0), (0, 40))
    a.connector("slide", origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="slide")
    ra = a.slide_rail("slide", "a", 40, width=8, height=4, flare=flare)
    b = Part("slide_b_%d" % flare)
    block(b, "frame", (-15, 15), (0, 12), (-20, 60))
    b.connector("slide", origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="slide")
    rb = b.slide_rail("slide", "b", None, width=8, height=4, flare=flare)
    return a, b, ra, rb


@case
def slide_pair():
    a, b, ra, rb = slide_parts()
    out = {"clearance": float(rb.clearance), "a": run(a, {"rail_a_height": 5}), "b": run(b)}
    out["common"] = {dz: common(b, a, App.Placement(App.Vector(0, 0, dz), App.Rotation())) for dz in (0, 20, -20)}
    c = out["clearance"]
    out["probes_b"] = inside(b, (0, 2, 0), (0, 4 + c / 2 - 0.05, 0), (0, 4 + c / 2 + 0.1, 0), (5.0, 3.9, 0),
                             (5.5, 3.9, 0))
    out["probes_a"] = inside(a, (4.9, 3.9, 20), (5.1, 3.9, 20), (0, -0.5, 20))
    flat_a, flat_b, _, _ = slide_parts(flare=0)
    out["flat_common"] = common(flat_b, flat_a)
    out["flat_a"] = run(flat_a, flex_runs=0)
    return out


def gear_part(name, teeth, mesh, mesh_angle, module=1.5):
    part = Part(name)
    part.connector("axle", origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="axle")
    g = part.spur_gear("axle", module, teeth, 6, mesh=mesh, mesh_angle=mesh_angle, name="gear")
    part.doc.recompute()
    return part, g


@case
def gear_pair():
    z1, z2, m = 12, 30, 1.5
    a, ga = gear_part("gear_a", z1, "tooth", 0)
    b, gb = gear_part("gear_b", z2, "gap", 180)
    out = {"ret": {k: float(getattr(gb, k)) for k in ("module", "teeth", "thick", "pitch_d", "tip_d", "root_d")},
           "types": [o.TypeId for o in a.body.Group]}
    cd = m * (z1 + z2) / 2 + 0.1
    sweep = {}
    for k in range(13):
        th = -30 + 5 * k
        sa = a.body.Shape.copy()
        sa.rotate(App.Vector(), App.Vector(0, 0, 1), th)
        sb = b.body.Shape.copy()
        sb.rotate(App.Vector(), App.Vector(0, 0, 1), -th * z1 / z2)
        sb.translate(App.Vector(cd, 0, 0))
        sweep[th] = sa.common(sb).Volume
    out["mesh_common"] = sweep
    wrong = b.body.Shape.copy()
    wrong.rotate(App.Vector(), App.Vector(0, 0, 1), 180.0 / z2)   # gap -> tooth facing the driver
    wrong.translate(App.Vector(cd, 0, 0))
    out["wrong_phase_common"] = a.body.Shape.common(wrong).Volume
    out["a"] = run(a, {"gear_module": 2})
    out["b"] = run(b)
    out["holes"] = cyl_diams(a)
    out["tooth_on_x"] = inside(a, (m * (z1 + 2) / 2 - 0.4 * m, 0, 3))[0]
    out["b_gap_toward_a"] = not inside(b, (-(m * (z2 + 2) / 2 - 0.4 * m), 0, 3))[0]
    return out


def snap_parts(material="PETG", **kw):
    a = Part("lid_part", material=material)
    block(a, "lid", (-10, 0), (-8, 8), (-5, 0))
    a.connector("latch", origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="latch")
    sa = a.snap_hook("latch", "a", 15, 2, 8, **kw)
    b = Part("wall_part", material=material)
    block(b, "wall", (0, 3), (-10, 10), (2, 20))
    b.connector("latch", origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="latch")
    sb = b.snap_hook("latch", "b", 15, 2, 8, **kw)
    return a, b, sa, sb


@case
def snap_pair():
    a, b, sa, sb = snap_parts()
    out = {"ret": {k: float(getattr(sa, k)) for k in ("length", "thick", "width", "overhang", "lip_h", "strain")},
           "a": run(a, {"snap_a_overhang": 1.2}), "b": run(b)}
    out["common"] = common(a, b)
    out["probes_a"] = inside(a, (0.3, 0, 14), (0.8, 0, 14), (-1, 0, 7), (0.5, 0, 7), (-5, 0, -0.5))
    out["probes_b"] = inside(b, (0.5, 0, 14), (0.5, 0, 10), (2, 0, 14), (0.5, 0, 15.5))
    return out


@case
def errors():
    def err(fn):
        try:
            fn()
        except SDKError as e:
            return e.signal
        return {"cause": "no error"}

    def conn(name, material="PETG"):
        p = Part(name, material=material)
        block(p, name + "_b", (-5, 5), (-5, 5), (0, 5))
        p.connector("conn", origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="conn")
        return p
    return {
        "hinge_side": err(lambda: conn("e_a").hinge("conn", "c", 30)),
        "hinge_knuckles": err(lambda: conn("e_b").hinge("conn", "a", 30, knuckles=1)),
        "hinge_pin": err(lambda: conn("e_c").hinge("conn", "a", 30, pin="nail")),
        "hinge_short": err(lambda: conn("e_d").hinge("conn", "a", 2, knuckles=5)),
        "rail_no_length": err(lambda: conn("e_e").slide_rail("conn", "a")),
        "gear_teeth": err(lambda: conn("e_f").spur_gear("conn", 1.5, 7.5, 6)),
        "gear_module": err(lambda: conn("e_g").spur_gear("conn", 0.5, 20, 6)),
        "snap_strain": err(lambda: conn("e_h").snap_hook("conn", "a", 6, 3, 8)),
        "snap_pla": err(lambda: conn("e_i", "PLA").snap_hook("conn", "a", 16, 2.5, 8, overhang=1.2)),
        "snap_petg_ok": err(lambda: conn("e_j").snap_hook("conn", "a", 16, 2.5, 8, overhang=1.2)),
    }


for name, fn in CASES.items():
    try:
        result[name] = fn()
    except Exception as e:
        result[name] = {"error": "%s: %s" % (type(e).__name__, e), "signal": getattr(e, "signal", None),
                        "traceback": traceback.format_exc()}
