"""Worker ops for the assembly gates (P1 T1.7). Run inside freecadcmd.

sweep_assembly request:
    plan, parts, components     as build_assembly (assembly/writer.py)
    poses                       [{joint: q}, ...]  rad / mm; coupled joints follow (Tree.joint_values)
    allow_overlap               [[owner, owner], ...]  pairs exempt from interference (plan.allow_overlap)
    min_volume_mm3              overlap threshold (default 1.0)
  -> result = {tree, frames {owner: {conn: 4x4}}, mounts {component: conn},
               owners {owner: {volume_mm3, area_mm2, com_mm, inertia_mm5 (about the CoM, density 1), bbox}},
               collisions [{pose, a, b, volume_mm3, bbox {owner: [xmin, ymin, zmin, xmax, ymax, zmax] in its part frame}}], n_poses, n_checks}
     Signals: tree problems (gate L5). Interference is reported as data; validate/assembly_check.py
     turns it into signals.
    Owners are placed by our FK (D15), no Assembly WB solve: the residual gate in build_assembly already
    ties the WB to the same math. Pairs inside one rigid group (fixed joints, component attaches) cannot
    move against each other and are checked at pose 0 only.

mesh_parts request:
    meshes          {name: .FCStd}      out_dir     directory for <name>.stl
    linear_deflection_mm (default 0.05), angular_deflection_rad (default 0.25)
  -> result = {name: {stl, n_facets}}; STL in the owner frame, mm.
"""
import itertools
import os

import FreeCAD as App
import MeshPart

from assembly import owners as O
from assembly.kinematics import GATE, Tree


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

    collisions, n_checks, poses = [], 0, req.get("poses") or [{}]
    for i, pose in enumerate(poses):
        values, psigs = tree.joint_values(pose)
        for s in psigs:
            s["location"] = "pose %d" % i
        sigs.extend(psigs)
        if any(s["level"] == "failure" for s in psigs):
            continue
        world = tree.fk(values)
        placed = {}
        for o, s in shapes.items():
            ws = s.copy(False)
            ws.Placement = O.to_placement(world[o])
            placed[o] = ws
        for a, b in pairs:
            if i > 0 and group[a] == group[b]:
                continue
            n_checks += 1
            if not placed[a].BoundBox.intersect(placed[b].BoundBox):
                continue
            inter = placed[a].common(placed[b])
            if inter.Volume > min_vol:
                collisions.append({"pose": i, "q": values, "a": a, "b": b, "volume_mm3": inter.Volume,
                                   "bbox": {o: _local_bbox(inter, world[o]) for o in (a, b)}})
    res["result"].update({"owners": props, "collisions": collisions, "n_poses": len(poses), "n_checks": n_checks,
                          "groups": group})
    res["metrics"] = {"n_owners": len(shapes), "n_pairs": len(pairs), "n_checks": n_checks,
                      "n_collisions": len(collisions)}
    res["ok"] = not any(s["level"] == "failure" for s in sigs)
    _close([doc] + opened)


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
            result[name] = {"stl": stl, "n_facets": mesh.CountFacets}
        finally:
            App.closeDocument(d.Name)
    res["result"] = result
    res["ok"] = not any(s["level"] == "failure" for s in res["signals"])
