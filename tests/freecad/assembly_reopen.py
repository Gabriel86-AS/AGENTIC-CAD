"""Reopen a saved assembly.FCStd (run by tests/test_assembly.py): links resolve, joints solve, and part
param edits propagate through the joints after solve(). The WB does not hold joint values: a free
slider absorbs a change along its axis, so the check is "still on the rail axis", not "same q"."""
import FreeCAD as App
import UtilsAssembly

doc = App.openDocument(request["fcstd"])
asm = doc.getObject("Assembly")
links = [o for o in asm.Group if o.TypeId == "App::Link"]
result["links"] = {o.Label: (o.LinkedObject.Document.FileName if o.LinkedObject else None) for o in links}
result["joints"] = sorted(o.Label for o in doc.getObject("Joints").Group)
result["solve"] = asm.solve()


def frame(owner, conn):
    return UtilsAssembly.getGlobalPlacement([doc.getObject(owner), [conn + ".", conn + "."]])


for name, alias, value in (("arm", "length", "=150 mm"), ("base", "thick", "=14 mm")):
    part = App.getDocument(name)
    sheet = part.getObject("Params")
    sheet.set(sheet.getCellFromAlias(alias), value)
    part.recompute()
doc.recompute()
result["solve_after_edit"] = asm.solve()
doc.recompute()
rail, car = frame("arm", "rail"), frame("carriage", "rail_in")
d = car.Base - rail.Base
axis = rail.Rotation.multVec(App.Vector(0, 0, 1))
result["rail"] = list(rail.Base)
result["off_axis_mm"] = (d - axis * d.dot(axis)).Length
result["rot_deg"] = rail.Rotation.inverted().multiply(car.Rotation).Angle * 57.29577951308232
for doc_ in list(App.listDocuments().values()):
    App.closeDocument(doc_.Name)
