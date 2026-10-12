"""Offscreen renders for the vision gates (P2 §3.4, L3v / L8), orchestrator side, PyVista on VTK.

    render_views(items, frames, out_dir, stem)  -> {view: png}
      items    [{"mesh": stl path | trimesh | pv.PolyData, "color": optional, "transform": optional 4x4}]
      frames   [(name, o, z, x)] connector frames drawn as RGB axes (x red, y green = z × x, blue z), labelled
      views    the fixed camera set VIEWS (iso ×2, front, top); 768 px, white background, neutral grey,
               feature edges dark so the shape reads like a CAD view, parallel projection
    render_part(fcstd, connectors, io_dir, out_dir)  worker op mesh_parts -> STL -> render_views

VTK is not safe to drive from several threads at once (part agents run in parallel), so one lock guards
every render.
"""
import threading
from pathlib import Path

import numpy as np
import pyvista as pv

from harness.run import run_request

SIZE = 768
COLOR = "#c9ccd1"
EDGE_COLOR = "#30343a"
FEATURE_DEG = 30.0
AXIS_COLORS = ("#e53935", "#43a047", "#1e63e9")   # x, y, z
# view -> (camera direction from the focal point, up vector), part frame
VIEWS = {
    "iso": ((1.0, -1.0, 0.8), (0, 0, 1)),
    "iso_back": ((-1.0, 1.0, 0.8), (0, 0, 1)),
    "front": ((0.0, -1.0, 0.0), (0, 0, 1)),
    "top": ((0.0, 0.0, 1.0), (0, 1, 0)),
}
CAPTIONS = {"iso": "iso from +x -y +z", "iso_back": "iso from -x +y +z", "front": "front: looking along +y",
            "top": "top: looking down -z"}
_LOCK = threading.Lock()


def _poly(item) -> pv.PolyData:
    m = item["mesh"]
    if isinstance(m, (str, Path)):
        poly = pv.read(str(m))
    elif isinstance(m, pv.PolyData):
        poly = m.copy()
    else:   # trimesh
        faces = np.hstack([np.full((len(m.faces), 1), 3), np.asarray(m.faces)]).ravel()
        poly = pv.PolyData(np.asarray(m.vertices, float), faces)
    if item.get("transform") is not None:
        poly = poly.transform(np.asarray(item["transform"], float), inplace=False)
    return poly


def _frame_axes(p: pv.Plotter, frames, length: float):
    for name, o, z, x in frames:
        o, z, x = (np.asarray(v, float) for v in (o, z, x))
        y = np.cross(z, x)
        for d, c in zip((x, y, z), AXIS_COLORS):
            p.add_mesh(pv.Arrow(start=o, direction=d, scale=length, tip_length=0.25, tip_radius=0.08, shaft_radius=0.03),
                       color=c, lighting=False)
        p.add_point_labels([o + z * length * 1.05], [name], font_size=13, text_color="black", point_size=1,
                           shape_color="white", shape_opacity=0.7, always_visible=True, show_points=False)


def _camera(pts: np.ndarray, d, up, diag: float, margin: float = 1.15):
    """Parallel camera along -d that frames every point (VTK reset_camera fits a sphere and clips long parts)."""
    back = np.asarray(d, float) / np.linalg.norm(d)
    right = np.cross(-back, up)
    right /= np.linalg.norm(right)
    upv = np.cross(right, -back)
    c = pts.mean(axis=0)
    r, u = (pts - c) @ right, (pts - c) @ upv
    focal = c + right * (r.max() + r.min()) / 2 + upv * (u.max() + u.min()) / 2
    scale = margin * max(r.max() - r.min(), u.max() - u.min(), 1.0) / 2
    return focal, focal + back * diag * 3, upv, scale


def render_views(items: list[dict], frames=(), out_dir: Path = Path("."), stem: str = "part",
                 views=tuple(VIEWS), size: int = SIZE, title: str = "") -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    polys = [(_poly(it), it.get("color") or COLOR) for it in items]
    pts = np.vstack([q.points for q, _ in polys] + [np.asarray(f[1], float)[None] for f in frames])
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    center, diag = (lo + hi) / 2, float(np.linalg.norm(hi - lo)) or 1.0
    length = max(5.0, 0.12 * diag)
    tips = [np.asarray(f[1], float) + length * 1.3 * np.asarray(a, float)
            for f in frames for a in (f[2], f[3], np.cross(f[2], f[3]))]
    fit = np.vstack([pts] + [t[None] for t in tips])
    out = {}
    with _LOCK:
        p = pv.Plotter(off_screen=True, window_size=(size, size))
        try:
            p.set_background("white")
            for q, color in polys:
                p.add_mesh(q, color=color, smooth_shading=False, specular=0.15, ambient=0.25,
                           silhouette={"color": EDGE_COLOR, "line_width": 1.5})
                edges = q.extract_feature_edges(feature_angle=FEATURE_DEG, boundary_edges=True,
                                                non_manifold_edges=False, manifold_edges=False)
                if edges.n_points:
                    p.add_mesh(edges, color=EDGE_COLOR, line_width=1.5, lighting=False)
            _frame_axes(p, frames, length)
            p.add_axes(line_width=3, labels_off=False)
            p.enable_parallel_projection()
            caption = None
            for v in views:
                focal, pos, up, scale = _camera(fit, *VIEWS[v], diag)
                p.camera_position = [tuple(pos), tuple(focal), tuple(up)]
                p.camera.parallel_scale = scale
                p.reset_camera_clipping_range()
                if caption is not None:
                    p.remove_actor(caption)
                caption = p.add_text(("%s  |  %s" % (title, CAPTIONS[v])) if title else CAPTIONS[v],
                                     position="upper_left", font_size=11, color="black")
                png = out_dir / ("%s_%s.png" % (stem, v))
                p.screenshot(str(png))
                out[v] = png
        finally:
            p.close()
    return out


def render_part(fcstd: Path, connectors: dict, io_dir: Path, out_dir: Path, name: str | None = None,
                views=tuple(VIEWS), timeout_s: float = 60.0) -> tuple[dict[str, Path], list]:
    """connectors: {name: {o, z, x, ...}} (compile report or plan). Returns ({view: png}, worker signals)."""
    name = name or Path(fcstd).stem
    out_dir = Path(out_dir).resolve()
    res = run_request({"op": "mesh_parts", "meshes": {name: str(Path(fcstd).resolve())}, "out_dir": str(out_dir)},
                      io_dir, timeout_s=timeout_s)
    m = (res.result or {}).get(name)
    if not res.ok or not m:
        return {}, res.signals
    frames = [(n, c["o"], c["z"], c["x"]) for n, c in sorted(connectors.items())]
    return render_views([{"mesh": m["stl"]}], frames, out_dir, name, views=views, title=name), res.signals
