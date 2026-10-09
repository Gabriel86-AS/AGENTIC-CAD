"""Load the owners of a plan inside freecadcmd: parts and component instances (shared by the worker ops
build_assembly and sweep_assembly).

Each owner is one App::Part: a compiled part's container, a card model's container, or (cards without a
generator) an envelope App::Part made in `host` with one LCS per card connector and a box for
`interfaces.volume`. Connector frames are read from the LCS placements (owner frame, mm).
"""
import os

import FreeCAD as App
import Part as OCC

from assembly.kinematics import GATE, frame, identity
from components.card import connectors as card_connectors
from components.card import load_card, resolve


def _signal(level, cause, location="", action=""):
    return {"level": level, "gate": GATE, "cause": cause, "location": location, "action": action}


def to_matrix(plc):
    m = plc.toMatrix()
    return [[m.A11, m.A12, m.A13, m.A14], [m.A21, m.A22, m.A23, m.A24],
            [m.A31, m.A32, m.A33, m.A34], [0.0, 0.0, 0.0, 1.0]]


def to_placement(t):
    return App.Placement(App.Matrix(*[t[i][j] for i in range(4) for j in range(4)]))


def _container(doc):
    parts = [o for o in doc.Objects if o.TypeId == "App::Part"]
    return parts[0] if len(parts) == 1 else None


def _is_identity(t, tol=1e-9):
    i = identity()
    return all(abs(t[r][c] - i[r][c]) < tol for r in range(4) for c in range(4))


def envelope(host, cid, card):
    """App::Part with one LCS per card connector and a box for interfaces.volume (if the card has one).
    `host` is an Assembly (newObject) or a document (addObject)."""
    new = host.addObject if isinstance(host, App.Document) else host.newObject
    part = new("App::Part", cid)
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


def lcs_names(container):
    """connector name -> LCS object name (envelope LCS are prefixed to stay unique; Label is the connector)."""
    return {o.Label: o.Name for o in container.Group if o.TypeId == "App::LocalCoordinateSystem"}


def owner_shape(container):
    """Solids of an owner in its own frame (bodies and envelope boxes; datums and LCS excluded), or None.
    A feature's .Shape excludes the parent App::Part placement (D21), so this is the owner frame."""
    solids = []
    for o in container.Group:
        if o.TypeId in ("PartDesign::Body", "Part::Box") and not o.Shape.isNull():
            solids.extend(o.Shape.Solids)
    return OCC.makeCompound(solids) if solids else None


def load(plan, parts, components, host, sigs):
    """-> (containers, frames, lcs, mounts, opened docs). `parts` {name: .FCStd}, `components` {id: {card, fcstd}}.
    Missing files and cards without a mount connector become failure signals in `sigs`."""
    containers, frames, lcs, mounts, opened = {}, {}, {}, {}, []
    for p in plan["parts"]:
        path = parts.get(p["name"])
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
        cid, info = comp["id"], components.get(comp["id"]) or {}
        card = load_card(info.get("card") or comp["card"])
        if info.get("fcstd"):
            d = App.openDocument(os.path.abspath(info["fcstd"]))
            opened.append(d)
            containers[cid] = _container(d)
        else:
            containers[cid] = envelope(host, cid, card)
    (host if isinstance(host, App.Document) else host.Document).recompute()

    for owner, c in containers.items():
        lcs[owner] = lcs_names(c)
        frames[owner] = {name: to_matrix(c.Document.getObject(obj).Placement) for name, obj in lcs[owner].items()}
    for comp in plan.get("components", []):
        cid = comp["id"]
        mount = [n for n, t in frames.get(cid, {}).items() if _is_identity(t)]
        if not mount:
            sigs.append(_signal("failure", "card %s has no mount connector at its frame origin" % comp["card"],
                                "components/%s.yaml" % comp["card"], "add a connector with o 0, z +z, x +x"))
        else:
            mounts[cid] = mount[0]
    return containers, frames, lcs, mounts, opened
