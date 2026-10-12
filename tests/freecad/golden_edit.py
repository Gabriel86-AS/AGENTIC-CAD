"""Reopen a golden assembly (run by tests/test_golden.py), edit a plan param and a part param like a user in the
GUI would, recompute + solve: every object stays valid and the tool connector follows both edits.
request: fcstd; optional tool_owner (default "arm", B1-3) and edits [[document, alias, value], ...]
(default: arm arm_len 180 mm, turret sh_h 45 mm), probes [[owner, connector], ...]: world origins after the
edit. The WB solver keeps free joints near their old placements, so in a longer chain an edit may also turn the
joints a little: compare pose-independent quantities (origins on joint axes, distances along a link)."""
import FreeCAD as App
import UtilsAssembly

doc = App.openDocument(request["fcstd"])
asm = doc.getObject("Assembly")


OWNER = request.get("tool_owner", "arm")
EDITS = request.get("edits") or [["arm", "arm_len", "=180 mm"], ["turret", "sh_h", "=45 mm"]]


def origin(owner, conn):
    return list(UtilsAssembly.getGlobalPlacement([doc.getObject(owner), [conn + ".", conn + "."]]).Base)


def tool():
    return origin(OWNER, "tool")


def invalid():
    return sorted("%s/%s" % (d.Name, o.Name) for d in App.listDocuments().values() for o in d.Objects
                  if "Invalid" in o.State or "Error" in o.State)


result["solve"] = asm.solve()
result["tool"] = tool()
for name, alias, value in EDITS:
    part = App.getDocument(name)
    sheet = part.getObject("Params")
    sheet.set(sheet.getCellFromAlias(alias), value)
    part.recompute()
doc.recompute()
result["solve_after_edit"] = asm.solve()
doc.recompute()
result["tool_after_edit"] = tool()
result["invalid"] = invalid()
result["probes"] = [origin(o, c) for o, c in request.get("probes") or []]
result["arm_solids"] = len(App.getDocument(OWNER).getObject("Body").Shape.Solids)
result["solids"] = {n: len(App.getDocument(n).getObject("Body").Shape.Solids) for n, _, _ in EDITS}
for doc_ in list(App.listDocuments().values()):
    App.closeDocument(doc_.Name)
