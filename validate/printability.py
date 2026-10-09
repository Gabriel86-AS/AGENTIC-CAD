"""L7-basic print-readiness (P1 §4.4), orchestrator side, against the active print profile.

    check_print(plan, parts, io_dir, mesh_dir)
      worker op mesh_parts -> <mesh_dir>/<part>.stl (part frame, mm), then per part with trimesh:
      watertight   closed, consistently wound, positive volume                  -> CODE
      build volume fits in some axis-aligned orientation (sorted extents)       -> DESIGN (split the part)
      min wall     ray cast from each facet inward along -normal; facets whose
                   thickness < profile min_wall cover > THIN_AREA_MM2           -> CODE
"""
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import trimesh

from harness.feedback import format_feedback
from harness.run import run_request
from profiles import load_profile
from schemas.plan import AssemblyPlan
from schemas.signal import Signal

THIN_AREA_MM2 = 1.0       # thin facets smaller than this in total are tessellation noise, not a wall
WALL_TOL_MM = 0.05
RAY_EPS_MM = 1e-3


@dataclass
class PrintCheck:
    ok: bool
    signals: list[Signal]
    parts: dict = field(default_factory=dict)      # name -> {stl, extents_mm, watertight, fits, min_wall_mm, thin_area_mm2}
    feedback: str = ""


def wall_thickness(mesh: trimesh.Trimesh) -> np.ndarray:
    """Per-facet material thickness (mm) along -normal; inf where the ray escapes (open mesh)."""
    origins = mesh.triangles_center - mesh.face_normals * RAY_EPS_MM
    dirs = -mesh.face_normals
    locs, idx, _ = mesh.ray.intersects_location(origins, dirs, multiple_hits=False)
    out = np.full(len(mesh.faces), np.inf)
    if len(idx):
        out[idx] = np.linalg.norm(locs - origins[idx], axis=1) + RAY_EPS_MM
    return out


def check_mesh(name: str, mesh: trimesh.Trimesh, profile: dict) -> tuple[dict, list[Signal]]:
    sigs, loc = [], "parts/%s.py" % name
    ext = [float(v) for v in mesh.extents]
    watertight = bool(mesh.is_watertight and mesh.is_winding_consistent and mesh.volume > 0)
    if not watertight:
        sigs.append(Signal(level="failure", gate="L7", location=loc, cause="%s mesh is not a closed solid" % name,
                           action="look for zero-thickness faces or touching bodies in the part",
                           evidence={"watertight": bool(mesh.is_watertight), "winding": bool(mesh.is_winding_consistent)}))
    bv = profile["build_volume_mm"]
    fits = all(e <= b + 1e-6 for e, b in zip(sorted(ext), sorted(bv)))
    if not fits:
        sigs.append(Signal(level="failure", gate="L7", location=loc,
                           cause="%s is %s mm; it fits the %s mm build volume in no axis-aligned orientation"
                                 % (name, [round(e, 1) for e in ext], bv),
                           action="split the part with a joint feature (screws, dovetail) or shorten it",
                           evidence={"extents_mm": ext, "build_volume_mm": bv}))
    min_wall = profile["min_wall_mm"]
    thick = wall_thickness(mesh) if watertight else np.full(len(mesh.faces), np.inf)
    thin = thick < min_wall - WALL_TOL_MM
    thin_area = float(mesh.area_faces[thin].sum())
    finite = thick[np.isfinite(thick)]
    info = {"extents_mm": ext, "watertight": watertight, "fits": fits, "volume_mm3": float(mesh.volume),
            "min_wall_mm": float(finite.min()) if len(finite) else None, "thin_area_mm2": thin_area}
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
    return info, sigs


def check_print(plan: AssemblyPlan, parts: dict[str, Path], io_dir: Path, mesh_dir: Path,
                timeout_s: float = 120.0) -> PrintCheck:
    profile = load_profile(plan.requirements.print_profile)
    res = run_request({"op": "mesh_parts", "meshes": {k: str(Path(v).resolve()) for k, v in parts.items()},
                       "out_dir": str(Path(mesh_dir).resolve())}, io_dir, timeout_s=timeout_s)
    sigs = [Signal.from_worker(s) for s in res.signals]
    out = PrintCheck(False, sigs)
    for name, m in (res.result or {}).items():
        mesh = trimesh.load_mesh(m["stl"])
        info, psigs = check_mesh(name, mesh, profile)
        out.parts[name] = {"stl": m["stl"], **info}
        sigs += psigs
    out.ok = res.ok and not any(s.level == "failure" for s in sigs)
    out.feedback = format_feedback("check_print", sigs, "%d parts meshed, profile %s" % (len(out.parts), profile["id"]))
    return out
