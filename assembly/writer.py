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
import UtilsAssembly

from assembly import owners as O
from assembly.kinematics import GATE, Tree, mul, residual
from assembly.owners import to_matrix, to_placement

POS_TOL_MM = 1e-4
ROT_TOL_DEG = 1e-4
WB_TYPE = {"fixed": "Fixed", "revolute": "Revolute", "continuous": "Revolute", "gears": "Revolute",
           "slider": "Slider", "rack_pinion": "Slider", "cylindrical": "Cylindrical", "ball": "Ball"}


def _signal(level, cause, location="", action=""):
    return {"level": level, "gate": GATE, "cause": cause, "location": location, "action": action}


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

    containers, frames, lcs, mounts, opened = O.load(plan, req["parts"], req["components"], asm, sigs)

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
        shape = o.Shape if o.TypeId == "App::Link" else _placed(O.owner_shape(o), o.Placement)
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


def _placed(shape, plc):
    return shape.transformGeometry(plc.toMatrix()) if shape else None


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
    """Close each document once (two components of one card share its model document)."""
    names = []
    for d in [doc] + opened:
        if d.Name not in names:
            names.append(d.Name)
    for n in names:
        if n in App.listDocuments():
            App.closeDocument(n)
