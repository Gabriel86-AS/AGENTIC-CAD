"""T0.7 helper (runs in freecadcmd): meshes + inertials for the hand-written 1-joint URDF.

request: {"servo_fcstd": ".../servo_mg996r.FCStd", "card": ".../mg996r.yaml", "out_dir": ".../meshes"}
Links:
  base_link  MG996R body, frame = card frame (body_mount); mass = card mass_kg (uniform density)
  arm_link   60 x 10 x 4 mm PETG bar, frame = horn connector (sits on the spline tip, +x along the bar)
Meshes are written in metres (unit conversion lives in the exporter, P0 §4), so the URDF needs no scale.
Inertials follow S9: computed on the link's own shape in the link frame; inertia about the CoM.
"""
import os

import FreeCAD as App
import MeshPart
import Part

from components.card import load_card

PETG_KG_MM3 = 1.27e-6
ARM = (60.0, 10.0, 4.0)
OUT = request["out_dir"]
os.makedirs(OUT, exist_ok=True)


def inertial(shape, density_kg_mm3):
    s = shape.Solids[0] if len(shape.Solids) == 1 else shape
    m = density_kg_mm3 * shape.Volume
    I = s.MatrixOfInertia  # mm^5, about CoM, density 1
    k = density_kg_mm3 * 1e-6  # -> kg m^2
    return {
        "mass_kg": m,
        "com_m": [c * 1e-3 for c in s.CenterOfMass],
        "ixx": I.A11 * k, "ixy": I.A12 * k, "ixz": I.A13 * k,
        "iyy": I.A22 * k, "iyz": I.A23 * k, "izz": I.A33 * k,
    }


def write_stl_m(shape, name):
    m = shape.copy()
    m.scale(1e-3)  # mm -> m
    mesh = MeshPart.meshFromShape(Shape=m, LinearDeflection=0.05e-3, AngularDeflection=0.3, Relative=False)
    path = os.path.join(OUT, name + ".stl")
    mesh.write(path)
    return {"path": path, "facets": mesh.CountFacets, "solid": mesh.isSolid()}


card = load_card(request["card"])
doc = App.openDocument(request["servo_fcstd"])
servo = doc.getObject("Body").Shape.copy()  # Body placement is identity in the card frame
App.closeDocument(doc.Name)

arm = Part.makeBox(*ARM, App.Vector(-ARM[1] / 2, -ARM[1] / 2, 0))  # horn hub over the shaft, bar along +x

result["base_link"] = {**inertial(servo, card["mass_kg"] / servo.Volume), "mesh": write_stl_m(servo, "mg996r")}
result["arm_link"] = {**inertial(arm, PETG_KG_MM3), "mesh": write_stl_m(arm, "arm")}
result["pass"] = result["base_link"]["mesh"]["solid"] and result["arm_link"]["mesh"]["solid"]
