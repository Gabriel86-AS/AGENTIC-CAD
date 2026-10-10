"""Reopen the golden B1-3 assembly (run by tests/test_golden.py), edit a plan param (arm_len) and a part
param (turret sh_h) like a user in the GUI would, recompute + solve: every object stays valid and the tool
connector follows both edits."""
import FreeCAD as App
import UtilsAssembly

doc = App.openDocument(request["fcstd"])
asm = doc.getObject("Assembly")


def tool():
    return list(UtilsAssembly.getGlobalPlacement([doc.getObject("arm"), ["tool.", "tool."]]).Base)


def invalid():
    return sorted("%s/%s" % (d.Name, o.Name) for d in App.listDocuments().values() for o in d.Objects
                  if "Invalid" in o.State or "Error" in o.State)


result["solve"] = asm.solve()
result["tool"] = tool()
for name, alias, value in (("arm", "arm_len", "=180 mm"), ("turret", "sh_h", "=45 mm")):
    part = App.getDocument(name)
    sheet = part.getObject("Params")
    sheet.set(sheet.getCellFromAlias(alias), value)
    part.recompute()
doc.recompute()
result["solve_after_edit"] = asm.solve()
doc.recompute()
result["tool_after_edit"] = tool()
result["invalid"] = invalid()
result["arm_solids"] = len(App.getDocument("arm").getObject("Body").Shape.Solids)
for doc_ in list(App.listDocuments().values()):
    App.closeDocument(doc_.Name)
