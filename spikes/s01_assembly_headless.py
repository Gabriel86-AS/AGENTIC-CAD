"""S1: do Assembly WB joints work in freecadcmd (GuiUp = False)?

Pattern from AssemblyTests/TestCore.py: two boxes, ground one, revolute on faces.
Pass: no exception and assembly.solve() returns 0.
"""
import FreeCAD as App
import JointObject
import UtilsAssembly  # noqa: F401  (import itself is part of the test)

result["gui_up"] = bool(App.GuiUp)

doc = App.newDocument("S1")
assembly = doc.addObject("Assembly::AssemblyObject", "Assembly")
joints = assembly.newObject("Assembly::JointGroup", "Joints")

base = assembly.newObject("Part::Box", "Base")
child = assembly.newObject("Part::Box", "Child")
child.Placement = App.Placement(App.Vector(30, 5, 0), App.Rotation(15, 0, 0))
doc.recompute()

grounded = joints.newObject("App::FeaturePython", "GroundedJoint")
JointObject.GroundedJoint(grounded, base)

rev = joints.newObject("App::FeaturePython", "Revolute")
JointObject.Joint(rev, JointObject.JointTypes.index("Revolute"))
# Top face of base (Face6, z=10) to bottom face of child (Face5, z=0)
rev.Proxy.setJointConnectors(rev, [[base, ["Face6", "Vertex7"]], [child, ["Face5", "Vertex1"]]])

solve_code = assembly.solve()
doc.recompute()

result["joint_types"] = list(JointObject.JointTypes)
result["solve_code"] = solve_code
result["child_placement"] = str(child.Placement)
result["placement1"] = str(rev.Placement1)
result["placement2"] = str(rev.Placement2)

# Placement1/2 are expressed in their part's frame (see S4), so the solved
# joint frames must coincide in world space.
w1 = base.Placement.multiply(rev.Placement1)
w2 = child.Placement.multiply(rev.Placement2)
z1 = w1.Rotation.multVec(App.Vector(0, 0, 1))
z2 = w2.Rotation.multVec(App.Vector(0, 0, 1))
result["world_frame1"] = str(w1)
result["world_frame2"] = str(w2)
result["pos_err_mm"] = (w1.Base - w2.Base).Length
result["z_dot"] = z1.dot(z2)
result["child_moved"] = (child.Placement.Base - App.Vector(30, 5, 0)).Length > 1e-6
result["pass"] = (
    solve_code == 0 and result["child_moved"] and result["pos_err_mm"] < 1e-6 and abs(abs(result["z_dot"]) - 1) < 1e-9
)
