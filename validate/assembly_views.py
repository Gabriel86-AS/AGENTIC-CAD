"""L8 review inputs (P2 §3.4, plan §4.1): the assembled design as pictures and a table, orchestrator side.

    prepare(plan, parts, check, io_dir, out_dir) -> ReviewInputs
      meshes    worker op mesh_parts on the compiled parts + card models (owner frame, mm); cards without a
                model get their envelope box from the owner bbox (as in the URDF)
      placement our FK (assembly/kinematics.Tree with the built connector frames of the L5 check), rest pose
      colours   one per link (rigid group, = URDF link); purchased components in a dark tone per kind
      renders   iso, iso_back, front, top at rest (validate/render.render_views), owners labelled, each joint
                drawn as RGB axes at its parent connector (z = the joint axis), the tool frame too
      sweeps    per independent moving joint: FRAMES frames over its limits (the others at rest, coupled joints
                follow), one camera for all frames -> <joint>.gif (people) + a 2 x 2 sheet of KEYFRAMES of
                them (the model: the backends send PNG only)
      table     per owner: kind, link, colour, world AABB size and centre at rest (ArtiCAD's bbox table)

    out_dir/views/<view>.png  out_dir/sweeps/<joint>.gif|_sheet.png|_NN.png  out_dir/inputs.json
"""
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pyvista as pv
import trimesh

from assembly.builder import card_model
from assembly.kinematics import LINEAR, Tree
from components.card import load_card
from export.urdf import rigid_groups
from harness.run import run_request
from schemas.plan import AssemblyPlan
from schemas.signal import Signal
from validate.plan_check import tool_connectors
from validate.poses import linspace, rest_pose, sampled_joints
from validate.render import VIEWS, contact_sheet, render_views, write_gif

LINK_COLORS = ("#9fb7d9", "#e3b07a", "#9fd3a5", "#d9a0c4", "#d6cf86", "#8fd0d0", "#c4a7e7", "#e89a9a")
CARD_COLORS = {"actuator": "#3c4048", "bearing": "#8c96a0", "electronics": "#3f7d5a"}
CARD_COLOR = "#6b6f76"
FRAMES, KEYFRAMES, SWEEP_SIZE = 12, (0, 4, 7, 11), 512
MESH = {"linear_deflection_mm": 0.1, "angular_deflection_rad": 0.3}   # coarser than L7: pictures only


@dataclass
class ReviewInputs:
    ok: bool
    signals: list[Signal]
    renders: dict = field(default_factory=dict)     # view -> png (rest pose)
    sweeps: list[dict] = field(default_factory=list)  # [{joint, type, driver, unit, lower, upper, values, keys, gif, sheet}]
    table: list[dict] = field(default_factory=list)   # [{owner, kind, link, color, size_mm, center_mm}]
    elapsed_s: float = 0.0

    def images(self) -> list[Path]:
        """What the judge sees, in order: the rest views, then one keyframe sheet per joint."""
        return [Path(self.renders[v]) for v in VIEWS if v in self.renders] + [Path(s["sheet"]) for s in self.sweeps]


def _card_kind(card_id: str) -> str:
    return str(load_card(card_id).get("kind", ""))


def _box(bb) -> trimesh.Trimesh:
    lo, hi = np.asarray(bb[:3], float), np.asarray(bb[3:], float)
    return trimesh.creation.box(extents=np.maximum(hi - lo, 1e-3), transform=trimesh.transformations.translation_matrix((lo + hi) / 2))


def owner_meshes(plan: AssemblyPlan, parts: dict, owners: dict, io_dir: Path, mesh_dir: Path,
                 timeout_s: float = 180.0) -> tuple[dict, list[Signal]]:
    """{owner: trimesh (owner frame, mm)} for every part and component; envelopes as boxes."""
    sources = {k: str(Path(v).resolve()) for k, v in parts.items()}
    for c in plan.components:
        m = card_model(c.card)
        if m:
            sources[c.id] = m
    res = run_request({"op": "mesh_parts", "meshes": sources, "out_dir": str(Path(mesh_dir).resolve()), **MESH},
                      io_dir, timeout_s=timeout_s)
    sigs = [Signal.from_worker(s) for s in res.signals]
    out = {o: trimesh.load_mesh(m["stl"]) for o, m in (res.result or {}).items()}
    for c in plan.components:
        bb = (owners.get(c.id) or {}).get("bbox")
        if c.id not in out and bb:
            out[c.id] = _box(bb)
    return out, sigs


def _world(pts: np.ndarray, t) -> np.ndarray:
    t = np.asarray(t, float)
    return np.asarray(pts, float) @ t[:3, :3].T + t[:3, 3]


def _corners(mesh: trimesh.Trimesh) -> np.ndarray:
    lo, hi = mesh.bounds
    return np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])


def _label_at(w: np.ndarray) -> np.ndarray:
    """Owner label anchor: its most camera-facing point in the main (iso) view, pulled a quarter back towards its
    box centre, so a label lands on visible material rather than on whatever sits over the box centre."""
    d = np.asarray(VIEWS["iso"][0], float)
    c = (w.min(axis=0) + w.max(axis=0)) / 2
    return c + 0.75 * (w[int(np.argmax(w @ d))] - c)


def _fmt_q(v: float, linear: bool) -> str:
    return "%.1f mm" % v if linear else "%.1f°" % math.degrees(v)


def prepare(plan: AssemblyPlan, parts: dict, frames: dict, mounts: dict, owners: dict, io_dir: Path, out_dir: Path,
            sweeps: bool = True) -> ReviewInputs:
    """parts: {part: .FCStd}; frames / mounts / owners: the L5 check's built connector frames, component mount
    connectors and owner mass properties (JobReport carries them)."""
    t0 = time.monotonic()
    out_dir = Path(out_dir).resolve()
    pd = plan.model_dump()
    tree = Tree(pd, frames, mounts)
    if not tree.ok:
        return ReviewInputs(False, [Signal(level="failure", gate="L8", cause=s["cause"], location=s["location"])
                                    for s in tree.signals])
    meshes, sigs = owner_meshes(plan, parts, owners, io_dir, out_dir / "meshes")
    group = rigid_groups(tree)
    links = list(dict.fromkeys(group[o] for o in [tree.ground] + [e.child for e in tree.edges]))
    kinds = {c.id: c.card for c in plan.components}
    colors = {}
    for o in meshes:
        if o in kinds:
            k = _card_kind(kinds[o])
            colors[o] = next((v for p, v in CARD_COLORS.items() if k.startswith(p)), CARD_COLOR)
        else:
            colors[o] = LINK_COLORS[links.index(group.get(o, tree.ground)) % len(LINK_COLORS)]
    polys = {o: pv.wrap(m) for o, m in meshes.items()}
    rest = rest_pose(pd)
    values, _ = tree.joint_values(rest)
    world = tree.fk(values)

    table = []
    for o in [tree.ground] + [e.child for e in tree.edges]:
        if o not in meshes:
            continue
        w = _world(meshes[o].vertices, world[o])
        lo, hi = w.min(axis=0), w.max(axis=0)
        table.append({"owner": o, "kind": kinds.get(o, "part"), "link": group.get(o, o), "color": colors[o],
                      "size_mm": [round(float(v), 1) for v in hi - lo],
                      "center_mm": [round(float(v), 1) for v in (hi + lo) / 2]})
    labels = [(o, _label_at(_world(meshes[o].vertices, world[o]))) for o in (r["owner"] for r in table)]
    jframes = []
    for e in tree.edges:
        if e.kind == "joint" and e.jtype != "fixed":
            t = np.asarray(world[e.parent], float) @ np.asarray(frames[e.parent][e.pconn], float)
            jframes.append((e.name, t[:3, 3], t[:3, 2], t[:3, 0]))
    for ref in tool_connectors(plan)[:1]:
        o, c = ref.split(".")
        if o in world:
            t = np.asarray(world[o], float) @ np.asarray(frames[o][c], float)
            jframes.append((ref, t[:3, 3], t[:3, 2], t[:3, 0]))
    items = [{"mesh": polys[o], "color": colors[o], "transform": world[o]} for o in polys if o in world]
    renders = render_views(items, jframes, out_dir / "views", "assembly", labels=labels,
                           title="assembly at rest") if items else {}

    out_sweeps = []
    for name, lo, hi in (sampled_joints(pd) if sweeps and items else []):
        j = next(x for x in plan.joints if x.name == name)
        linear = j.type in LINEAR
        qs = linspace(lo, hi, FRAMES)
        poses = [tree.fk(tree.joint_values(dict(rest, **{name: q}))[0]) for q in qs]
        fit = np.vstack([_world(_corners(meshes[o]), w[o])
                         for w in poses for o in meshes if o in w])
        sdir = out_dir / "sweeps"
        pngs = []
        for i, (q, w) in enumerate(zip(qs, poses)):
            its = [{"mesh": polys[o], "color": colors[o], "transform": w[o]} for o in polys if o in w]
            png = render_views(its, (), sdir, "%s_%02d" % (name, i), views=("iso",), size=SWEEP_SIZE, fit=fit,
                               title="%s = %s" % (name, _fmt_q(q, linear)))["iso"]
            pngs.append(png)
        out_sweeps.append({"joint": name, "type": j.type, "driver": j.driver, "unit": "mm" if linear else "deg",
                           "lower": lo, "upper": hi, "values": qs, "keys": [qs[k] for k in KEYFRAMES],
                           "gif": str(write_gif(pngs, sdir / f"{name}.gif")),
                           "sheet": str(contact_sheet([pngs[k] for k in KEYFRAMES], sdir / f"{name}_sheet.png"))})
    ok = bool(renders) and not any(s.level == "failure" for s in sigs)
    res = ReviewInputs(ok, sigs, renders, out_sweeps, table, round(time.monotonic() - t0, 2))
    (out_dir / "inputs.json").write_text(json.dumps({"renders": {k: str(v) for k, v in renders.items()},
                                                     "sweeps": out_sweeps, "table": table, "elapsed_s": res.elapsed_s,
                                                     "signals": [s.model_dump() for s in sigs]}, indent=1, default=str),
                                         encoding="utf-8")
    return res


def table_markdown(table: list[dict]) -> str:
    rows = ["| owner | kind | link | colour | size x y z (mm) | centre x y z (mm) |", "|---|---|---|---|---|---|"]
    rows += ["| %s | %s | %s | %s | %s | %s |" % (r["owner"], r["kind"], r["link"], r["color"],
                                                " × ".join("%.1f" % v for v in r["size_mm"]),
                                                ", ".join("%.1f" % v for v in r["center_mm"])) for r in table]
    return "\n".join(rows)
