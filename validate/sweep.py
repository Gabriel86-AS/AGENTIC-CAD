"""Worker ops for the assembly gates (P1 T1.7). Run inside freecadcmd.

sweep_assembly request:
    plan, parts, components     as build_assembly (assembly/writer.py)
    poses                       [{joint: q}, ...]  rad / mm; coupled joints follow (Tree.joint_values)
    allow_overlap               [[owner, owner], ...]  pairs exempt from interference (plan.allow_overlap)
    min_volume_mm3              overlap threshold (default 1.0)
    clearance_window_mm         min clearance is measured (OCCT distToShape) only for pairs whose bounding
                                boxes come within this distance (default 5.0); farther pairs report None
    probes                      [{a, b, poses: [{joint: q}], near: [owner, connector, radius_mm] (optional)}]:
                                distance of one pair at each given pose (run_tests); with `near`, only the
                                material of `a` within radius_mm of that connector's origin counts
  -> result = {tree, frames {owner: {conn: 4x4}}, mounts {component: conn},
               owners {owner: {volume_mm3, area_mm2, com_mm, inertia_mm5 (about the CoM, density 1), bbox}},
               collisions [{pose, a, b, volume_mm3, bbox {owner: [xmin, ymin, zmin, xmax, ymax, zmax] in its part frame}}],
               clearances [{a, b, min_mm (0 when they overlap, None if never within the window), pose, q, n_near}]
                   for pairs that move against each other (different rigid groups, not exempt),
               probes [{a, b, min_mm, pose, q, points [[x, y, z], [x, y, z]] (world, closest points)} | {a, b, error}],
               n_poses, n_checks}
     Signals: tree problems (gate L5). Interference is reported as data; validate/assembly_check.py
     turns it into signals.
    Owners are placed by our FK (D15), no Assembly WB solve: the residual gate in build_assembly already
    ties the WB to the same math. Pairs inside one rigid group (fixed joints, component attaches) cannot
    move against each other and are checked at pose 0 only. A pair's overlap and distance depend only on
    its relative placement, so they are computed once per distinct relative placement (a gear pair under a
    cover that swings on its own is not re-cut at every cover pose).

mesh_parts request:
    meshes          {name: .FCStd}      out_dir     directory for <name>.stl
    linear_deflection_mm (default 0.05), angular_deflection_rad (default 0.25)
  -> result = {name: {stl, n_facets, holes}}; STL in the owner frame, mm.
     holes = [{d, axis, center, length}]: internal cylindrical faces grouped by axis line and radius whose
     angular span adds up to a full circle (fits probe, L7); axis sign-normalised, center at mid-length.
"""
import itertools
import math
import os

import FreeCAD as App
import MeshPart
import Part as OCC

from assembly import owners as O
from assembly.kinematics import GATE, Tree, mul, origin


def _signal(level, cause, location="", action=""):
    return {"level": level, "gate": GATE, "cause": cause, "location": location, "action": action}


def _vec(v):
    return [float(v.x), float(v.y), float(v.z)]


def mass_props(shape):
    """Volume, area, CoM and inertia about the CoM (density 1, mm^5) of a compound of solids."""
    solids = list(shape.Solids)
    vol = sum(s.Volume for s in solids)
    if vol <= 0:
        return None
    com = [sum(s.Volume * _vec(s.CenterOfMass)[k] for s in solids) / vol for k in range(3)]
    inertia = [[0.0] * 3 for _ in range(3)]
    for s in solids:
        m = s.MatrixOfInertia
        local = [[m.A11, m.A12, m.A13], [m.A21, m.A22, m.A23], [m.A31, m.A32, m.A33]]
        d = [c - g for c, g in zip(_vec(s.CenterOfMass), com)]
        dd = sum(x * x for x in d)
        for i in range(3):
            for j in range(3):
                inertia[i][j] += local[i][j] + s.Volume * ((dd if i == j else 0.0) - d[i] * d[j])
    bb = shape.BoundBox
    return {"volume_mm3": vol, "area_mm2": sum(s.Area for s in solids), "com_mm": com, "inertia_mm5": inertia,
            "bbox": [bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax]}


def _local_bbox(shape, world_tf):
    """Bounding box of a world-placed shape in an owner's part frame (where the part agent writes geometry)."""
    loc = shape.copy(False)
    loc.Placement = O.to_placement(world_tf).inverse().multiply(loc.Placement)
    b = loc.BoundBox
    return [round(v, 2) for v in (b.XMin, b.YMin, b.ZMin, b.XMax, b.YMax, b.ZMax)]


def rigid_groups(tree, owners):
    """owner -> group id; owners joined by attach edges or fixed joints share a group."""
    parent = {o: o for o in owners}

    def find(o):
        while parent[o] != o:
            parent[o] = parent[parent[o]]
            o = parent[o]
        return o

    for e in tree.edges:
        if (e.kind == "attach" or e.jtype == "fixed") and e.parent in parent and e.child in parent:
            parent[find(e.child)] = find(e.parent)
    return {o: find(o) for o in owners}


def _close(docs):
    """Close each document once (two components of one card share its model document)."""
    names = []
    for d in docs:
        if d.Name not in names:
            names.append(d.Name)
    for n in names:
        if n in App.listDocuments():
            App.closeDocument(n)


def _place(shapes, world):
    placed = {}
    for o, s in shapes.items():
        ws = s.copy(False)
        ws.Placement = O.to_placement(world[o])
        placed[o] = ws
    return placed


def _rel_key(wa, wb):
    """Relative placement of b in a's frame, rounded (memo key for the pair checks)."""
    rel = O.to_placement(wa).inverse().multiply(O.to_placement(wb))
    return tuple(round(v, 6) for v in (*rel.Base, *rel.Rotation.Q))


def _probe(p, shapes, tree):
    """Closest distance of owners a and b over the probe's poses (0 when they overlap)."""
    a, b, near = p["a"], p["b"], p.get("near")
    out = {"a": a, "b": b, "min_mm": None, "pose": None, "q": None, "points": None}
    missing = [o for o in (a, b) if o not in shapes]
    if missing:
        out["error"] = "no solid for %s" % ", ".join(missing)
        return out
    for i, pose in enumerate(p.get("poses") or [{}]):
        values, psigs = tree.joint_values(pose)
        if any(s["level"] == "failure" for s in psigs):
            out["error"] = "pose %d: %s" % (i, psigs[0]["cause"])
            return out
        world = tree.fk(values)
        placed = _place({a: shapes[a], b: shapes[b]}, world)
        if near:
            owner, conn, r = near
            at = origin(mul(world[owner], tree.frames[owner][conn]))
            placed[a] = placed[a].common(OCC.makeSphere(float(r), App.Vector(*at)))
            if placed[a].Volume <= 1e-9:
                out["error"] = "%s has no material within %g mm of %s.%s" % (a, r, owner, conn)
                return out
        d, pts, _ = placed[a].distToShape(placed[b])
        if out["min_mm"] is None or d < out["min_mm"]:
            out.update(min_mm=d, pose=i, q=values, points=[_vec(pts[0][0]), _vec(pts[0][1])] if pts else None)
    return out


def sweep_assembly(req, res):
    plan, sigs = req["plan"], res["signals"]
    doc = App.newDocument("sweep")
    containers, frames, _, mounts, opened = O.load(plan, req["parts"], req.get("components") or {}, doc, sigs)
    tree = Tree(plan, frames, mounts)
    sigs.extend(tree.signals)
    res["result"] = {"tree": tree.summary(), "frames": frames, "mounts": mounts}
    if any(s["level"] == "failure" for s in sigs):
        _close([doc] + opened)
        return

    shapes, props = {}, {}
    for owner, c in containers.items():
        s = O.owner_shape(c)
        props[owner] = mass_props(s) if s else None
        if s and props[owner]:
            shapes[owner] = s
    group = rigid_groups(tree, list(shapes))
    exempt = {frozenset(p) for p in req.get("allow_overlap") or []}
    pairs = [(a, b) for a, b in itertools.combinations(sorted(shapes), 2) if frozenset((a, b)) not in exempt]
    min_vol = float(req.get("min_volume_mm3", 1.0))

    window = float(req.get("clearance_window_mm", 5.0))
    clear = {(a, b): {"a": a, "b": b, "min_mm": None, "pose": None, "q": None, "n_near": 0}
             for a, b in pairs if group[a] != group[b]}
    collisions, n_checks, poses = [], 0, req.get("poses") or [{}]
    memo, n_reused = {}, 0          # (a, b, relative placement) -> {"vol", "bbox", "d"}
    for i, pose in enumerate(poses):
        values, psigs = tree.joint_values(pose)
        for s in psigs:
            s["location"] = "pose %d" % i
        sigs.extend(psigs)
        if any(s["level"] == "failure" for s in psigs):
            continue
        world = tree.fk(values)
        placed = _place(shapes, world)
        for a, b in pairs:
            if i > 0 and group[a] == group[b]:
                continue
            n_checks += 1
            box = App.BoundBox(placed[a].BoundBox)
            box.enlarge(window)
            if not box.intersect(placed[b].BoundBox):
                continue
            key = (a, b, _rel_key(world[a], world[b]))
            m = memo.get(key)
            if m is None:
                m = memo[key] = {"vol": None, "bbox": None, "d": None}
            else:
                n_reused += 1
            d = None
            if placed[a].BoundBox.intersect(placed[b].BoundBox):
                if m["vol"] is None:
                    inter = placed[a].common(placed[b])
                    m["vol"] = inter.Volume
                    if inter.Volume > min_vol:
                        m["bbox"] = {o: _local_bbox(inter, world[o]) for o in (a, b)}
                if m["vol"] > min_vol:
                    collisions.append({"pose": i, "q": values, "a": a, "b": b, "volume_mm3": m["vol"],
                                       "bbox": m["bbox"]})
                    d = 0.0
            c = clear.get((a, b))
            if c is None:
                continue
            if d is None:
                if m["d"] is None:
                    m["d"] = placed[a].distToShape(placed[b])[0]
                d = m["d"]
            c["n_near"] += 1
            if c["min_mm"] is None or d < c["min_mm"]:
                c.update(min_mm=d, pose=i, q=values)
    probes = [_probe(p, shapes, tree) for p in req.get("probes") or []]
    res["result"].update({"owners": props, "collisions": collisions, "clearances": list(clear.values()),
                          "probes": probes, "n_poses": len(poses), "n_checks": n_checks, "groups": group})
    res["metrics"] = {"n_owners": len(shapes), "n_pairs": len(pairs), "n_checks": n_checks,
                      "n_collisions": len(collisions), "n_reused": n_reused}
    res["ok"] = not any(s["level"] == "failure" for s in sigs)
    _close([doc] + opened)


def _axis_key(axis, loc):
    """Sign-normalised axis + the axis line's point closest to the origin: one key per hole line."""
    a = [axis.x, axis.y, axis.z]
    if next(c for c in a if abs(c) > 1e-9) < 0:
        a = [-c for c in a]
    p = [loc.x, loc.y, loc.z]
    t = sum(pc * ac for pc, ac in zip(p, a))
    return a, [pc - t * ac for pc, ac in zip(p, a)]


def holes(shape, min_span_deg=300.0):
    """Internal cylinders (normal toward the axis) whose faces cover >= min_span_deg around one axis line."""
    groups = {}
    for f in shape.Faces:
        srf = f.Surface
        if not isinstance(srf, OCC.Cylinder):
            continue
        u0, u1, v0, v1 = f.ParameterRange
        um, vm = (u0 + u1) / 2, (v0 + v1) / 2
        pt, n = f.valueAt(um, vm), f.normalAt(um, vm)
        axis, loc = srf.Axis, srf.Center
        a, p0 = _axis_key(axis, loc)
        rel = pt - loc
        radial = rel - axis * rel.dot(axis)
        if radial.Length < 1e-9 or n.dot(radial) >= 0:      # outer cylinder (boss, rounded corner outside)
            continue
        key = (round(srf.Radius, 3),) + tuple(round(c, 3) for c in a + p0)
        g = groups.setdefault(key, {"r": srf.Radius, "a": a, "p0": p0, "span": 0.0, "t": []})
        g["span"] += math.degrees(abs(u1 - u0))
        for v in (v0, v1):
            q = f.valueAt(um, v)
            g["t"].append(sum((qc - pc) * ac for qc, pc, ac in zip(_vec(q), g["p0"], g["a"])))
    out = []
    for g in groups.values():
        if g["span"] < min_span_deg - 1e-6:
            continue
        t0, t1 = min(g["t"]), max(g["t"])
        out.append({"d": round(2 * g["r"], 4), "axis": [round(c, 6) for c in g["a"]],
                    "center": [round(pc + (t0 + t1) / 2 * ac, 4) for pc, ac in zip(g["p0"], g["a"])],
                    "length": round(t1 - t0, 4)})
    return sorted(out, key=lambda h: (h["d"], h["center"]))


def mesh_parts(req, res):
    out_dir = os.path.abspath(req["out_dir"])
    os.makedirs(out_dir, exist_ok=True)
    lin = float(req.get("linear_deflection_mm", 0.05))
    ang = float(req.get("angular_deflection_rad", 0.25))
    result = {}
    for name, path in req["meshes"].items():
        d = App.openDocument(os.path.abspath(path))
        try:
            c = O._container(d)
            shape = O.owner_shape(c) if c else None
            if shape is None:
                res["signals"].append(_signal("failure", "%s: no solid to mesh" % path, name))
                continue
            mesh = MeshPart.meshFromShape(Shape=shape, LinearDeflection=lin, AngularDeflection=ang, Relative=False)
            stl = os.path.join(out_dir, name + ".stl")
            mesh.write(stl)
            result[name] = {"stl": stl, "n_facets": mesh.CountFacets, "holes": holes(shape)}
        finally:
            App.closeDocument(d.Name)
    res["result"] = result
    res["ok"] = not any(s["level"] == "failure" for s in res["signals"])
