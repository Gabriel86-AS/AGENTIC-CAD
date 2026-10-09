"""Worker op build_assembly (P1 T1.6): plan + compiled parts -> assembly.FCStd. Runs inside freecadcmd.

request:
    plan        AssemblyPlan.model_dump()
    parts       {part name: .FCStd}
    components  {component id: {"card": card .yaml path, "fcstd": card model .FCStd or null}}
    poses       [{joint: q}, ...]   rad / mm; missing joints = 0; poses[0] is the pose saved (default [{}])
    out         assembly .FCStd path
  -> result = {tree, poses: [{q, solve, max_pos_mm, max_rot_deg}], owners: {name: {placement, bbox}}},
     artifacts.fcstd. Signals: gate L5 (tree, pose limits, residual gate).

Method (D14, D15, D32): every owner is an `App::Link` to the App::Part in its own .FCStd (cards without a
generator get an envelope App::Part inside the assembly). Our FK (assembly/kinematics.py) is the pose
authority: write every link Placement and `joint.Offset1 = M(q)`, then `assembly.solve()`, then the
residual gate checks every joint/attach pair of connector frames in world. A solve return code of 0
alone proves nothing (S2).
"""
import os

import FreeCAD as App
import JointObject
import Part as OCC
import UtilsAssembly

from assembly.kinematics import GATE, Tree, frame, identity, mul, residual
from components.card import connectors as card_connectors
from components.card import load_card, resolve

POS_TOL_MM = 1e-4
ROT_TOL_DEG = 1e-4
WB_TYPE = {"fixed": "Fixed", "revolute": "Revolute", "continuous": "Revolute", "gears": "Revolute",
           "slider": "Slider", "rack_pinion": "Slider", "cylindrical": "Cylindrical", "ball": "Ball"}


def _signal(level, cause, location="", action=""):
    return {"level": level, "gate": GATE, "cause": cause, "location": location, "action": action}


def to_matrix(plc):
    m = plc.toMatrix()
    return [[m.A11, m.A12, m.A13, m.A14], [m.A21, m.A22, m.A23, m.A24],
            [m.A31, m.A32, m.A33, m.A34], [0.0, 0.0, 0.0, 1.0]]


def to_placement(t):
    m = App.Matrix(*[t[i][j] for i in range(4) for j in range(4)])
    return App.Placement(m)


def _container(doc):
    parts = [o for o in doc.Objects if o.TypeId == "App::Part"]
    return parts[0] if len(parts) == 1 else None


def _lcs_frames(container):
    return {o.Name: to_matrix(o.Placement) for o in container.Group
            if o.TypeId == "App::LocalCoordinateSystem"}


def _is_identity(t, tol=1e-9):
    i = identity()
    return all(abs(t[r][c] - i[r][c]) < tol for r in range(4) for c in range(4))


def _envelope(asm, cid, card):
    """App::Part with one LCS per card connector and a box for interfaces.volume (if the card has one)."""
    part = asm.newObject("App::Part", cid)
    for name, c in card_connectors(card).items():
        lcs = part.newObject("App::LocalCoordinateSystem", "%s_%s" % (cid, name))
        lcs.Label = name
        lcs.Placement = to_placement(frame(c["o"], c["z"], c["x"]))
        lcs.addProperty("App::PropertyString", "ConnectorLabel", "AgenticCAD").ConnectorLabel = c.get("label", "")
    vol = (card.get("interfaces") or {}).get("volume")
    if vol:
        v = resolve(card, vol)
        (cx, cy), (l, w, h) = v["centre_xy"], v["size"]
        box = part.newObject("Part::Box", cid + "_envelope")
        box.Length, box.Width, box.Height = l, w, h
        box.Placement = App.Placement(App.Vector(cx - l / 2, cy - w / 2, v.get("bottom_z", 0)), App.Rotation())
    return part


def _lcs_names(container):
    """connector name -> LCS object name (envelope LCS are prefixed to stay unique; Label is the connector)."""
    return {o.Label: o.Name for o in container.Group if o.TypeId == "App::LocalCoordinateSystem"}


def build_assembly(req, res):
    plan = req["plan"]
    out = os.path.abspath(req["out"])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if os.path.exists(out):
        os.remove(out)
    sigs = res["signals"]

    doc = App.newDocument("assembly")
    doc.saveAs(out)  # external links need a saved owner document (relative paths)
    asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
    jgroup = asm.newObject("Assembly::JointGroup", "Joints")
    opened = []

    containers, frames, lcs, mounts = {}, {}, {}, {}
    for p in plan["parts"]:
        path = req["parts"].get(p["name"])
        if not path or not os.path.exists(path):
            sigs.append(_signal("failure", "part %r has no compiled .FCStd (%s)" % (p["name"], path),
                                "parts/%s.py" % p["name"], "compile_part it first"))
            continue
        d = App.openDocument(os.path.abspath(path))
        opened.append(d)
        c = _container(d)
        if c is None:
            sigs.append(_signal("failure", "%s: expected one App::Part" % path, p["name"]))
            continue
        containers[p["name"]] = c
    for comp in plan.get("components", []):
        cid, info = comp["id"], req["components"].get(comp["id"]) or {}
        card = load_card(info.get("card") or comp["card"])
        if info.get("fcstd"):
            d = App.openDocument(os.path.abspath(info["fcstd"]))
            opened.append(d)
            containers[cid] = _container(d)
        else:
            containers[cid] = _envelope(asm, cid, card)
    doc.recompute()

    for owner, c in containers.items():
        lcs[owner] = _lcs_names(c)
        frames[owner] = {name: to_matrix(c.Document.getObject(obj).Placement) for name, obj in lcs[owner].items()}
    for comp in plan.get("components", []):
        cid = comp["id"]
        mount = [n for n, t in frames.get(cid, {}).items() if _is_identity(t)]
        if not mount:
            sigs.append(_signal("failure", "card %s has no mount connector at its frame origin" % comp["card"],
                                "components/%s.yaml" % comp["card"], "add a connector with o 0, z +z, x +x"))
        else:
            mounts[cid] = mount[0]

    tree = Tree(plan, frames, mounts)
    sigs.extend(tree.signals)
    res["result"] = {"tree": tree.summary()}
    if not tree.ok or any(s["level"] == "failure" for s in sigs):
        _close(doc, opened)
        return

    # owners: a link per external App::Part; envelopes are already inside the assembly
    objs = {}
    for owner, c in containers.items():
        if c.Document is doc:
            objs[owner] = c
        else:
            link = asm.newObject("App::Link", owner)
            link.LinkedObject = c
            link.Label = owner
            objs[owner] = link
    doc.recompute()

    g = jgroup.newObject("App::FeaturePython", "Ground")
    JointObject.GroundedJoint(g, objs[tree.ground])

    def ref(owner, conn):
        return [objs[owner], [lcs[owner][conn] + ".", lcs[owner][conn] + "."]]

    wb = {}
    for e in tree.edges:
        label = e.name if e.kind == "joint" else "attach_" + e.name  # labels are unique per document
        j = jgroup.newObject("App::FeaturePython", label)
        JointObject.Joint(j, JointObject.JointTypes.index(WB_TYPE[e.jtype]))
        j.Label = label
        j.Proxy.setJointConnectors(j, [ref(e.parent, e.pconn), ref(e.child, e.cconn)])
        j.addProperty("App::PropertyString", "PlanJoint", "AgenticCAD").PlanJoint = e.name
        j.addProperty("App::PropertyString", "PlanType", "AgenticCAD").PlanType = e.jtype if e.kind == "joint" else "attach"
        lim = e.joint.get("limits")
        if lim:
            j.addProperty("App::PropertyFloat", "PlanLower", "AgenticCAD").PlanLower = lim["lower"]
            j.addProperty("App::PropertyFloat", "PlanUpper", "AgenticCAD").PlanUpper = lim["upper"]
        wb[e.name] = j
    doc.recompute()

    poses = req.get("poses") or [{}]
    rows = [None] * len(poses)
    for i in reversed(range(len(poses))):  # poses[0] applied last: it is the saved pose
        values, psigs = tree.joint_values(poses[i])
        for s in psigs:
            s["location"] = "pose %d" % i
        sigs.extend(psigs)
        if any(s["level"] == "failure" for s in psigs):
            rows[i] = {"q": poses[i], "skipped": True}
            continue
        rows[i] = _apply_pose(asm, doc, tree, objs, ref, wb, values, i, sigs)

    doc.recompute()
    world = tree.fk(tree.joint_values(poses[0])[0])
    owners = {}
    for owner, o in objs.items():
        shape = o.Shape if o.TypeId == "App::Link" else _part_shape(o)
        bb = shape.BoundBox if shape and not shape.isNull() else None
        owners[owner] = {"placement": world[owner],
                         "bbox": [bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax] if bb else None}
    res["result"].update({"poses": rows, "owners": owners})
    res["metrics"] = {"n_owners": len(objs), "n_joints": sum(e.kind == "joint" for e in tree.edges),
                      "max_pos_mm": max([r.get("max_pos_mm", 0) for r in rows if r] or [0]),
                      "max_rot_deg": max([r.get("max_rot_deg", 0) for r in rows if r] or [0])}
    doc.save()
    res["artifacts"]["fcstd"] = out
    res["ok"] = not any(s["level"] == "failure" for s in sigs)
    _close(doc, opened)


def _part_shape(part):
    shapes = [o.Shape for o in part.Group if hasattr(o, "Shape") and o.TypeId == "Part::Box"]
    return OCC.makeCompound([s.transformGeometry(part.Placement.toMatrix()) for s in shapes]) if shapes else None


def _apply_pose(asm, doc, tree, objs, ref, wb, values, i, sigs):
    world = tree.fk(values)
    for e in tree.edges:  # Offsets first (each triggers a pre-solve), placements last (S4 preplace)
        wb[e.name].Offset1 = to_placement(tree.edge_motion(e, values))
    for owner, t in world.items():
        objs[owner].Placement = to_placement(t)
    code = asm.solve()
    doc.recompute()
    worst_pos, worst_rot, bad = 0.0, 0.0, []
    for e in tree.edges:
        parent = to_matrix(UtilsAssembly.getGlobalPlacement(ref(e.parent, e.pconn)))
        child = to_matrix(UtilsAssembly.getGlobalPlacement(ref(e.child, e.cconn)))
        pos, rot = residual(mul(parent, tree.edge_motion(e, values)), child)
        worst_pos, worst_rot = max(worst_pos, pos), max(worst_rot, rot)
        if pos > POS_TOL_MM or rot > ROT_TOL_DEG:
            bad.append((e, pos, rot))
    if code != 0:
        sigs.append(_signal("failure", "assembly.solve() returned %d at pose %d" % (code, i), "pose %d" % i,
                            "check the joint connectors: a solver failure means the frames cannot all coincide"))
    for e, pos, rot in bad:
        sigs.append(_signal("failure", "%s %r: connector frames %s / %s are %.4g mm, %.4g deg apart after solve"
                            % (e.kind, e.name, "%s.%s" % (e.parent, e.pconn), "%s.%s" % (e.child, e.cconn), pos, rot),
                            "pose %d" % i, "the Assembly WB solution differs from the mating rule; "
                            "check that the connector axes are unit and orthogonal"))
    return {"q": values, "solve": code, "max_pos_mm": worst_pos, "max_rot_deg": worst_rot}


def _close(doc, opened):
    for d in [doc] + opened:
        if d.Name in App.listDocuments():
            App.closeDocument(d.Name)
