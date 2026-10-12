"""L7-full print orientation and overhang analysis (P2 §3.3, plan §4.2). Pure Python: trimesh + shapely.

    candidates      6 axis-aligned (each part axis pointing down) + the largest convex-hull face down
    per candidate   rotate the mesh (print +z up, bed at z = 0) and measure
      fits          oriented bbox (min-area rectangle of the footprint, at any yaw on the bed, and height)
                    within the build volume
      contact       bed contact area (down faces at z = 0); < MIN_CONTACT_MM2 is an unstable candidate
      overhang      down faces steeper than max_overhang_deg from vertical (not on the bed), grouped into
                    connected regions. A region edge is anchored when the face across it runs downward
                    (a wall or a hole's lower half, i.e. material below the edge) or the edge lies on the bed. Overhang within
                    max_bridge_mm / 2 of an anchored edge prints (bridge between two walls, top of a horizontal
                    hole, short ledge); the rest needs support (cantilevers, pocket ceilings wider than a bridge)
      support       projected area of overhang beyond that reach (mm2); patches < MIN_SUPPORT_MM2 are noise
      load          sum over connector pairs of length * (load path . z)^2: layer lines are weak in tension,
                    so a load path along z is penalised (connector origins in the part frame)
    score = support + LOAD_LAMBDA_MM * load; rank: fits, stable, support within the L7 cap, score, then
                    lower and more bed contact.

Orientation = rotation matrix R (part frame -> print frame, before the shift to the bed).
"""
import math

import numpy as np
import trimesh
from shapely.geometry import LineString, MultiPoint, Polygon
from shapely.ops import unary_union

LOAD_LAMBDA_MM = 20.0     # 100 mm of load path standing along z costs as much as 2000 mm2 of support
MIN_CONTACT_MM2 = 10.0
MIN_SUPPORT_MM2 = 0.5
BED_TOL_MM = 0.01
SAME_DIR_COS = math.cos(math.radians(1.0))

AXES = {"-z": (0, 0, -1), "+z": (0, 0, 1), "-x": (-1, 0, 0), "+x": (1, 0, 0), "-y": (0, -1, 0), "+y": (0, 1, 0)}


def rotation_down(n) -> np.ndarray:
    """R with R @ n = (0, 0, -1): part direction n points at the bed."""
    n = np.asarray(n, float) / np.linalg.norm(n)
    down = np.array([0.0, 0.0, -1.0])
    c = float(n @ down)
    if c > 1 - 1e-12:
        return np.eye(3)
    if c < -1 + 1e-12:
        return np.diag([1.0, -1.0, -1.0])      # flip about x
    v = np.cross(n, down)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx / (1 + c)


def candidates(mesh: trimesh.Trimesh) -> list[tuple[str, np.ndarray]]:
    out = [(name + " down", rotation_down(n)) for name, n in AXES.items()]
    hull = mesh.convex_hull
    if len(hull.facets):
        i = int(np.argmax(hull.facets_area))
        n = hull.facets_normal[i]
        if all(abs(float(n @ np.asarray(a, float))) < SAME_DIR_COS for a in AXES.values()):
            out.append(("face %s down" % np.round(n, 3).tolist(), rotation_down(n)))
    return out


def _footprint(xy: np.ndarray) -> tuple[float, float, float]:
    """(short side, long side, area) of the min-area rectangle around the points, and the hull area."""
    hull = MultiPoint(xy).convex_hull
    if hull.geom_type != "Polygon":
        return 0.0, 0.0, 0.0
    rect = hull.minimum_rotated_rectangle
    c = np.asarray(rect.exterior.coords)[:3]
    a, b = np.linalg.norm(c[1] - c[0]), np.linalg.norm(c[2] - c[1])
    return float(min(a, b)), float(max(a, b)), float(hull.area)


def rect_fits(a: float, b: float, A: float, B: float, step_deg: float = 0.5) -> bool:
    """Does an a x b rectangle fit in an A x B one at some rotation (diagonal placement on the bed)?"""
    a, b = sorted((a, b))
    A, B = sorted((A, B))
    if a <= A + 1e-6 and b <= B + 1e-6:
        return True
    for t in np.radians(np.arange(step_deg, 90.0, step_deg)):
        c, s = math.cos(t), math.sin(t)
        if b * c + a * s <= B + 1e-6 and b * s + a * c <= A + 1e-6:
            return True
    return False


def _regions(mesh: trimesh.Trimesh, mask: np.ndarray) -> list[np.ndarray]:
    idx = np.flatnonzero(mask)
    if not len(idx):
        return []
    adj = mesh.face_adjacency
    keep = mask[adj[:, 0]] & mask[adj[:, 1]]
    return [np.asarray(c) for c in trimesh.graph.connected_components(adj[keep], nodes=idx, min_len=1)]


def analyse(mesh: trimesh.Trimesh, R: np.ndarray, profile: dict, load_paths: list) -> dict:
    """Measure one orientation. load_paths: [(p, q)] connector origin pairs in the part frame."""
    v = mesh.vertices @ R.T
    v = v - [0.0, 0.0, v[:, 2].min()]
    tri = v[mesh.faces]
    nz = (mesh.face_normals @ R.T)[:, 2]
    zc = tri[:, :, 2].max(axis=1)
    on_bed = (nz < -1 + 1e-6) & (zc < BED_TOL_MM)
    contact = float(mesh.area_faces[on_bed].sum())
    over = (nz < -math.sin(math.radians(profile["max_overhang_deg"]))) & ~on_bed
    region = np.full(len(mesh.faces), -1)
    regions = _regions(mesh, over)
    for k, faces in enumerate(regions):
        region[faces] = k
    anchors = [[] for _ in regions]
    adj, edges = mesh.face_adjacency, mesh.face_adjacency_edges
    for (f0, f1), e in zip(adj, edges):
        if (region[f0] >= 0) == (region[f1] >= 0):
            continue
        inside, other = (f0, f1) if region[f0] >= 0 else (f1, f0)
        a, b = v[e[0]], v[e[1]]
        if max(a[2], b[2]) < BED_TOL_MM or tri[other][:, 2].mean() < (a[2] + b[2]) / 2 - 1e-3:
            anchors[region[inside]].append(LineString([a[:2], b[:2]]))
    reach = profile["max_bridge_mm"] / 2
    support, bridges, worst = 0.0, 0, None
    for k, faces in enumerate(regions):
        poly = unary_union([g for g in (Polygon(t[:, :2]) for t in tri[faces]) if g.area > 1e-9])
        if poly.is_empty:
            continue
        free = poly.difference(unary_union(anchors[k]).buffer(reach)) if anchors[k] else poly
        area = float(free.area)
        if area < MIN_SUPPORT_MM2:
            bridges += 1
            continue
        support += area
        lo, hi = tri[faces].reshape(-1, 3).min(axis=0), tri[faces].reshape(-1, 3).max(axis=0)
        if worst is None or area > worst["area_mm2"]:
            worst = {"area_mm2": round(area, 2), "anchored": bool(anchors[k]), "z_mm": round(float(lo[2]), 2),
                     "region_min": lo.round(2).tolist(), "region_max": hi.round(2).tolist()}
    load = 0.0
    for p, q in load_paths:
        d = R @ (np.asarray(q, float) - np.asarray(p, float))
        L = float(np.linalg.norm(d))
        if L > 1.0:
            load += L * (d[2] / L) ** 2
    short, long_, hull_area = _footprint(v[:, :2])
    height = float(v[:, 2].max())
    bx, by, bz = profile["build_volume_mm"]
    fits = rect_fits(short, long_, bx, by) and height <= bz + 1e-6
    return {"fits": bool(fits), "size_mm": [round(short, 2), round(long_, 2), round(height, 2)],
            "contact_mm2": round(contact, 2), "footprint_mm2": round(hull_area, 2), "support_mm2": round(support, 2),
            "bridges": bridges, "worst_overhang": worst, "load_mm": round(float(load), 2),
            "score": round(float(support + LOAD_LAMBDA_MM * load), 2), "stable": bool(contact >= MIN_CONTACT_MM2)}


def best_orientation(mesh: trimesh.Trimesh, profile: dict, connectors: list, max_support_frac: float = 0.15) -> dict:
    """-> {name, R, ...analyse()} of the best candidate, plus `candidates` [{name, fits, stable, score, ...}].
    connectors: [(name, origin)] in the part frame; every pair is a load path. Ranking: fits, stable,
    support within max_support_frac x footprint (the L7 cap), then score, height, contact."""
    paths = [(a[1], b[1]) for i, a in enumerate(connectors) for b in connectors[i + 1:]]
    rows = []
    for name, R in candidates(mesh):
        rows.append({"name": name, "R": R, **analyse(mesh, R, profile, paths)})
    rank = sorted(rows, key=lambda r: (not r["fits"], not r["stable"],
                                       r["support_mm2"] > max(MIN_SUPPORT_MM2, max_support_frac * r["footprint_mm2"]),
                                       r["score"], r["size_mm"][2], -r["contact_mm2"]))
    best = dict(rank[0])
    best["R"] = best["R"].round(9).tolist()
    best["candidates"] = [{k: r[k] for k in ("name", "fits", "stable", "score", "support_mm2", "footprint_mm2", "load_mm",
                                             "contact_mm2", "size_mm")} for r in rows]
    return best
