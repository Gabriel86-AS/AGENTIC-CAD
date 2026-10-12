"""L7 print-readiness (P1 §4.4 basic, P2 §3.3 full), orchestrator side, against the active print profile.

    check_print(plan, parts, io_dir, mesh_dir, report=None)
      worker op mesh_parts -> <mesh_dir>/<part>.stl (part frame, mm) + its full cylindrical holes, then per part:
      watertight   closed, consistently wound, positive volume; manifold3d accepts it         -> CODE
      orientation  best of 6 axis-aligned + largest hull face down (validate/orient.py): fits, stable,
                   support <= cap first, then support area + load-path penalty; the part rotated onto the
                   bed is written to <mesh_dir>/print/<part>.stl
      build volume no candidate's oriented bbox fits the profile volume                          -> DESIGN (split)
      overhang     support area in the chosen orientation > cap = max_support_frac x footprint -> failure (CODE),
                   > SUPPORT_WARN x cap -> warning, > MIN_SUPPORT_MM2 -> note
      min wall     ray cast from each facet inward along -normal; facets whose
                   thickness < profile min_wall cover > THIN_AREA_MM2                            -> CODE
      fits         a hole at a bare mating nominal (screw thread, bearing seat outer_d) that no fit
                   produces: the printed hole shrinks, the part will not assemble                -> CODE;
                   a sliding/press fit hole horizontal in the chosen orientation without the profile's
                   horizontal extra -> warning
    report (path): print_report.json with the per-part facts and every orientation candidate.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

import manifold3d
import numpy as np
import trimesh

from components.card import card_ids, load_card, resolve
from harness.feedback import format_feedback
from harness.run import run_request
from profiles import hole_diameter, load_profile
from schemas.plan import AssemblyPlan
from schemas.signal import Signal
from validate.orient import MIN_SUPPORT_MM2, best_orientation

THIN_AREA_MM2 = 1.0       # thin facets smaller than this in total are tessellation noise, not a wall
WALL_TOL_MM = 0.05
RAY_EPS_MM = 1e-3
FIT_TOL_MM = 0.02
HORIZONTAL_COS = 0.1      # |axis . z| below this: the hole axis is horizontal on the bed
SLIDING_FITS = ("clearance", "close", "press")
SUPPORT_WARN = 0.25       # support below this fraction of the cap is a note, not a warning


@dataclass
class PrintCheck:
    ok: bool
    signals: list[Signal]
    parts: dict = field(default_factory=dict)      # name -> {stl, extents_mm, watertight, fits, min_wall_mm, ...,
    feedback: str = ""                             #          orientation {name, R, support_mm2, ...}, holes}


def wall_thickness(mesh: trimesh.Trimesh) -> np.ndarray:
    """Per-facet material thickness (mm) along -normal; inf where the ray escapes (open mesh)."""
    origins = mesh.triangles_center - mesh.face_normals * RAY_EPS_MM
    dirs = -mesh.face_normals
    locs, idx, _ = mesh.ray.intersects_location(origins, dirs, multiple_hits=False)
    out = np.full(len(mesh.faces), np.inf)
    if len(idx):
        out[idx] = np.linalg.norm(locs - origins[idx], axis=1) + RAY_EPS_MM
    return out


def is_manifold(mesh: trimesh.Trimesh) -> tuple[bool, str]:
    m = manifold3d.Manifold(manifold3d.Mesh(vert_properties=np.asarray(mesh.vertices, np.float32),
                                            tri_verts=np.asarray(mesh.faces, np.uint32)))
    st = m.status()
    return st == manifold3d.Error.NoError, str(st).split(".")[-1]


def fit_table(plan: AssemblyPlan | None, profile: dict) -> dict:
    """{"valid": {d: [labels]}, "bare": {d: label}}: diameters the profile fits produce (hole d -> what made it),
    and the bare mating nominals (screw threads, card bearing seats) no fit produces."""
    valid, nominals = {}, {}

    def add(d, label):
        valid.setdefault(round(d, 3), []).append(label)

    for thread, h in profile["screws"].items():
        nominals[float(thread[1:])] = thread
        for kind in ("clearance", "tap"):
            add(h[kind], "%s_%s" % (kind, thread.lower()))
            add(h[kind] + profile["horizontal_hole_extra_mm"], "%s_%s horizontal" % (kind, thread.lower()))
    for cid in card_ids():
        card = load_card(cid)
        hole = (card.get("interfaces") or {}).get("hole")
        if card.get("kind", "").startswith("fastener") and hole and "d" in hole:
            add(resolve(card, hole["d"]), "insert (%s)" % cid)
    seats = {}                                   # screws take screw fits; generic fits apply to card seats
    for comp in (plan.components if plan else []):
        card = load_card(comp.card)
        seat = (card.get("interfaces") or {}).get("seat") or {}
        if "outer_d" in seat:
            seats[resolve(card, seat["outer_d"])] = "%s seat (%s outer_d)" % (comp.id, comp.card)
    nominals.update(seats)
    for d in seats:
        for fit in profile["fits"]:
            add(hole_diameter(profile, d, fit), "%s %s" % (fit, nominals[d]))
            add(hole_diameter(profile, d, fit, horizontal=True), "%s %s horizontal" % (fit, nominals[d]))
    bare = {d: lab for d, lab in nominals.items() if not any(abs(d - v) < FIT_TOL_MM for v in valid)}
    return {"valid": valid, "bare": bare}


def _labels(table, d):
    return [lab for v, labs in table["valid"].items() if abs(v - d) < FIT_TOL_MM for lab in labs]


def check_holes(name: str, holes: list[dict], R, table: dict) -> list[Signal]:
    sigs, loc, sag = [], "parts/%s.py" % name, []
    for h in holes:
        bare = next((lab for n, lab in table["bare"].items() if abs(h["d"] - n) < FIT_TOL_MM), None)
        if bare:
            sigs.append(Signal(level="failure", gate="L7", location=loc,
                               cause="%s: hole d %.2f mm at %s (axis %s, part frame) equals the bare %s nominal; "
                                     "printed holes come out undersize, so it will not assemble"
                                     % (name, h["d"], h["center"], h["axis"], bare),
                               action="cut it with hole(fit=...): a screw fit like 'clearance_m3' / 'tap_m3' / "
                                      "'insert_m3', or d=<nominal> with fit='press' / 'clearance' (bearing_seat for bearings)",
                               evidence={"hole": h}))
            continue
        labs = _labels(table, h["d"])
        horizontal = abs(float((np.asarray(R) @ np.asarray(h["axis"]))[2])) < HORIZONTAL_COS
        if horizontal and labs and all(lab.split()[0].split("_")[0] in SLIDING_FITS and not lab.endswith("horizontal") for lab in labs):
            sag.append(h)
    if sag:
        sigs.append(Signal(level="warning", gate="L7", location=loc,
                           cause="%s: %d sliding/press fit hole(s) lie horizontal in the print orientation without the "
                                 "horizontal allowance (d %s mm); their tops sag" % (name, len(sag), sorted({h["d"] for h in sag})),
                           action="pass horizontal=True to hole()/hole_d() for these holes",
                           evidence={"holes": sag}))
    return sigs


def check_mesh(name: str, mesh: trimesh.Trimesh, profile: dict, connectors=(), holes=(), table=None) -> tuple[dict, list[Signal]]:
    """connectors: [(name, origin)] in the part frame (load paths); holes from the worker; table = fit_table()."""
    sigs, loc = [], "parts/%s.py" % name
    ext = [float(v) for v in mesh.extents]
    watertight = bool(mesh.is_watertight and mesh.is_winding_consistent and mesh.volume > 0)
    manifold, status = is_manifold(mesh) if watertight else (False, "skipped")
    if not watertight or not manifold:
        sigs.append(Signal(level="failure", gate="L7", location=loc, cause="%s mesh is not a closed manifold solid" % name,
                           action="look for zero-thickness faces or touching bodies in the part",
                           evidence={"watertight": bool(mesh.is_watertight), "winding": bool(mesh.is_winding_consistent),
                                     "manifold": status}))
    orient = best_orientation(mesh, profile, list(connectors), max_support_frac=profile.get("max_support_frac", 0.15))
    bv = profile["build_volume_mm"]
    fits = orient["fits"]
    if not fits:
        sigs.append(Signal(level="failure", gate="L7", location=loc,
                           cause="%s is %s mm; it fits the %s mm build volume in no print orientation"
                                 % (name, [round(e, 1) for e in ext], bv),
                           action="split it into two plan parts joined by a fixed joint at a 'split' connector "
                                  "(split_joint(by='dovetail'|'screws') on each half), or shorten it",
                           evidence={"extents_mm": ext, "build_volume_mm": bv}))
    cap = profile.get("max_support_frac", 0.15) * orient["footprint_mm2"]
    if orient["support_mm2"] > MIN_SUPPORT_MM2:
        w = orient["worst_overhang"]
        level = "failure" if orient["support_mm2"] > cap else "warning" if orient["support_mm2"] > SUPPORT_WARN * cap else "note"
        sigs.append(Signal(level=level, gate="L7", location=loc,
                           cause="%s needs %.0f mm2 of support printed %s (cap %.0f mm2 = %.0f %% of the %.0f mm2 footprint); "
                                 "largest unsupported overhang %.0f mm2 in x %.1f..%.1f, y %.1f..%.1f at z %.1f (print frame)"
                                 % (name, orient["support_mm2"], orient["name"], cap, 100 * profile.get("max_support_frac", 0.15),
                                    orient["footprint_mm2"], w["area_mm2"], w["region_min"][0], w["region_max"][0],
                                    w["region_min"][1], w["region_max"][1], w["z_mm"]),
                           action="bring floating faces down to the bed face, keep ceilings and spans <= %.0f mm between "
                                  "walls, or slope them to <= %.0f deg from vertical (chamfer)" % (profile["max_bridge_mm"], profile["max_overhang_deg"]),
                           evidence={"support_mm2": orient["support_mm2"], "cap_mm2": round(cap, 2), "orientation": orient["name"],
                                     "worst": w}))
    min_wall = profile["min_wall_mm"]
    thick = wall_thickness(mesh) if watertight else np.full(len(mesh.faces), np.inf)
    thin = thick < min_wall - WALL_TOL_MM
    thin_area = float(mesh.area_faces[thin].sum())
    finite = thick[np.isfinite(thick)]
    info = {"extents_mm": ext, "watertight": watertight, "manifold": manifold, "fits": fits, "volume_mm3": float(mesh.volume),
            "min_wall_mm": float(finite.min()) if len(finite) else None, "thin_area_mm2": thin_area,
            "orientation": orient, "holes": list(holes)}
    if thin_area > THIN_AREA_MM2:
        pts = mesh.triangles_center[thin]
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        sigs.append(Signal(level="failure", gate="L7", location=loc,
                           cause="%s has walls down to %.2f mm (< min_wall %.2f mm) over %.1f mm2, in x %.1f..%.1f, "
                                 "y %.1f..%.1f, z %.1f..%.1f (part frame)" % (name, float(thick[thin].min()), min_wall, thin_area,
                                                                               lo[0], hi[0], lo[1], hi[1], lo[2], hi[2]),
                           action="thicken the wall there or move the hole/pocket that thins it",
                           evidence={"min_mm": round(float(thick[thin].min()), 3), "area_mm2": round(thin_area, 2),
                                     "region_min": lo.round(2).tolist(), "region_max": hi.round(2).tolist()}))
    sigs += check_holes(name, list(holes), orient["R"], table or fit_table(None, profile))
    return info, sigs


def placed_on_bed(mesh: trimesh.Trimesh, R) -> trimesh.Trimesh:
    m = mesh.copy()
    T = np.eye(4)
    T[:3, :3] = R
    m.apply_transform(T)
    m.apply_translation([0.0, 0.0, -m.bounds[0][2]])
    return m


def check_print(plan: AssemblyPlan, parts: dict[str, Path], io_dir: Path, mesh_dir: Path,
                timeout_s: float = 120.0, report: Path | None = None) -> PrintCheck:
    profile = load_profile(plan.requirements.print_profile)
    mesh_dir = Path(mesh_dir).resolve()
    res = run_request({"op": "mesh_parts", "meshes": {k: str(Path(v).resolve()) for k, v in parts.items()},
                       "out_dir": str(mesh_dir)}, io_dir, timeout_s=timeout_s)
    sigs = [Signal.from_worker(s) for s in res.signals]
    out = PrintCheck(False, sigs)
    specs = {p.name: p for p in plan.parts}
    table = fit_table(plan, profile)
    (mesh_dir / "print").mkdir(parents=True, exist_ok=True)
    for name, m in (res.result or {}).items():
        mesh = trimesh.load_mesh(m["stl"])
        conns = [(c.name, c.o) for c in specs[name].connectors] if name in specs else []
        info, psigs = check_mesh(name, mesh, profile, conns, m.get("holes", []), table)
        stl = mesh_dir / "print" / (name + ".stl")
        placed_on_bed(mesh, info["orientation"]["R"]).export(stl)
        out.parts[name] = {"stl": m["stl"], "print_stl": str(stl), **info}
        sigs += psigs
    out.ok = res.ok and not any(s.level == "failure" for s in sigs)
    out.feedback = format_feedback("check_print", sigs, "%d parts meshed, profile %s" % (len(out.parts), profile["id"]))
    if report:
        Path(report).write_text(json.dumps({"ok": out.ok, "profile": profile["id"], "parts": out.parts,
                                            "signals": [s.model_dump() for s in sigs]}, indent=1, default=float),
                                encoding="utf-8")
    return out
