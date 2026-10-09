# FreeCAD Headless Scripting Architecture Guide (v1.0 / v1.1)

**Target Environment:** Agentic CAD / Headless Execution (`FreeCADCmd`)  
**Scope:** Assembly Workbench Automation & Sketcher Diagnostic API  
**Date Context:** Late 2026

> **Verification notes (corrected 2026-10-09).** This report was originally produced by a deep research tool. Several of its API claims did not match FreeCAD's source code, so they were checked against the official repository (`main` branch) and corrected:
> - `src/Mod/Assembly/JointObject.py` and `src/Mod/Assembly/AssemblyTests/TestCore.py` (joint creation, references, limits, grounding)
> - `src/Mod/Sketcher/App/SketchObject.pyi` and `SketchObject.h` (diagnostic attributes, `solve()` return codes)
>
> Corrected passages are marked **[CORRECTED]**. Claims still not confirmed against source or a test run are marked **[UNVERIFIED]**. The source is the `main` branch, so check names against the exact FreeCAD release you pin. Nothing here has been run yet; Phase 0 of the plan does that.
>
> **Phase 0 sweep (T0.5, 2026-10-09).** Every `[UNVERIFIED]` tag was resolved by running FreeCAD **1.1.4** headless (`freecadcmd`, Windows installer); evidence is in `docs/spike_results.md` (spikes S1–S10). Tags now read **[VERIFIED 1.1.4]**, **[FALSE → fallback]**, or **[N/A]** (not used by the AgenticCAD plan, so not tested).

---

## TOPIC A: FreeCAD Assembly Workbench (Headless Scripting)

### Executive Summary & Verdict
* **Verdict:** **Scriptable headless with workarounds** (unchanged).
* **Summary:** FreeCAD 1.0 introduced the integrated Assembly workbench, powered by the **OndselSolver** C++ engine. The assembly container (`Assembly::AssemblyObject`) and joint group (`Assembly::JointGroup`) are C++ objects. **[CORRECTED]** Joints are *not* a C++ `Assembly::JointObject` type: each joint is an `App::FeaturePython` object whose behaviour comes from the Python class `JointObject.Joint`, and grounding uses `JointObject.GroundedJoint`. The `JointObject` module imports `pivy` (Coin3D bindings) unconditionally and only imports `FreeCADGui` when `App.GuiUp` is true, so it can load without a GUI as long as `pivy` is importable. The official unit tests create joints this way. Creating and solving joints headless in `freecadcmd` is **[VERIFIED 1.1.4]** (spike S1).

---

### 1. Headless Assembly Creation Script **[CORRECTED]**

The pattern below follows `AssemblyTests/TestCore.py`. It has not been run yet.

```python
import FreeCAD as App
# Under FreeCADCmd these import with no sys.path changes  [VERIFIED 1.1.4]
import UtilsAssembly
import JointObject

doc = App.newDocument("HeadlessAssemblyDoc")

# 1. Assembly container and its joint group
assembly = doc.addObject("Assembly::AssemblyObject", "Assembly")
joints = assembly.newObject("Assembly::JointGroup", "Joints")

# 2. Parts live inside the assembly (the tests use Part::Box; App::Part / PartDesign::Body should also work)
base = assembly.newObject("Part::Box", "Base")
child = assembly.newObject("Part::Box", "Child")

# 3. Ground the base part
grounded = joints.newObject("App::FeaturePython", "GroundedJoint")
JointObject.GroundedJoint(grounded, base)          # adds the 'ObjectToGround' property

# 4. Revolute joint between the two parts
rev = joints.newObject("App::FeaturePython", "Revolute")
JointObject.Joint(rev, JointObject.JointTypes.index("Revolute"))

# References are [object, [element_sub, vertex_sub]]; the vertex fixes the joint origin on the element
refs = [
    [base,  ["Face6", "Vertex7"]],
    [child, ["Face5", "Vertex1"]],
]
rev.Proxy.setJointConnectors(rev, refs)            # sets Reference1/Reference2 and solves

# 5. Solve and read back
doc.recompute()                                    # or assembly.solve()
print(child.Placement)
```

Differences from the original report:
- `doc.addObject("Assembly::JointObject", ...)` does not exist. Joints are `App::FeaturePython` objects inside an `Assembly::JointGroup`, set up with `JointObject.Joint(obj, type_index)`.
- Grounding uses `JointObject.GroundedJoint(obj, part)`, not a `Fixed` joint against `(assembly, [""])`.
- References are `[obj, [element, vertex]]` (`App::PropertyXLinkSub`), set with `joint.Proxy.setJointConnectors(joint, refs)`.
- **[VERIFIED 1.1.4]** (S2) Reference a whole `App::LocalCoordinateSystem` inside an `App::Part` as `[part, ["LCS_x.", "LCS_x."]]`. **The trailing dot is required**: with `"LCS_x"` the solve still returns 0 but nothing mates, so always check frame coincidence after solving.

---

### 2. Setting Joint Values and Limits **[CORRECTED]**

Property names from `JointObject.py`:

| Purpose | Properties |
| :--- | :--- |
| References | `Reference1`, `Reference2` (`App::PropertyXLinkSub`) |
| Joint frames | `Placement1`, `Placement2` (computed from the references); `Detach1`, `Detach2` (when true, a placement is not recomputed) |
| Offsets | `Offset1`, `Offset2` (`App::PropertyPlacement`). The computed placement is multiplied by the offset |
| Scalar values | `Distance`, `Distance2` (`App::PropertyLength`), `Angle` (`App::PropertyAngle`) |
| Limits | `EnableLengthMin`, `EnableLengthMax`, `LengthMin`, `LengthMax` (Cylindrical, Slider); `EnableAngleMin`, `EnableAngleMax`, `AngleMin`, `AngleMax` (Revolute, Cylindrical). All `Enable*` default to `False` |

```python
rev.EnableAngleMin = True
rev.AngleMin = -90.0     # degrees
rev.EnableAngleMax = True
rev.AngleMax = 90.0
```

Corrections:
- `HasLimits`, `LowerLimit` and `UpperLimit` do not exist. Use the `Enable*` / `*Min` / `*Max` properties above.
- `Angle` is used by the **Angle** joint type only. It does not drive a Revolute joint, and `Distance` is not the driving property of a Slider. `Distance` is used by Distance, RackPinion, Screw, Gears and Belt; `Distance2` by Gears and Belt.
- **[VERIFIED 1.1.4]** (S3) `joint.Offset1 = Placement(0, Rotation(Z, q))` then `assembly.solve()` poses a revolute at +q (`Offset2 = Rz(-q)` is equivalent). Setting `AngleMin = AngleMax = q` does **not** drive the joint.
- **[FALSE → fallback]** (S4) Offset-only driving is not enough for chains: with two or more revolutes the distal link can land 180° off (solve 0) because pre-solve picks "matched or flipped" from the current pose. Fallback in use: compute every part placement with our own forward kinematics, write the placements and offsets, then solve and check residuals.

---

### 3. Joint Types **[CORRECTED]**

`JointObject.JointTypes`, in index order:

`"Fixed"`, `"Revolute"`, `"Cylindrical"`, `"Slider"`, `"Ball"`, `"Distance"`, `"Parallel"`, `"Perpendicular"`, `"Angle"`, `"RackPinion"`, `"Screw"`, `"Gears"`, `"Belt"`

Use `JointObject.JointTypes.index("Revolute")` rather than hard-coded indexes. RackPinion, Screw, Gears and Belt are coupling joints that the original report omitted. They matter for gear-driven robot mechanisms, but URDF has no equivalent (only `<mimic>` approximates a fixed ratio).

---

### 4. Module Architecture & Headless Dependencies **[CORRECTED]**

* **C++ / App-level (headless):** `App::Part`, `App::LocalCoordinateSystem`, `Assembly::AssemblyObject` (including its `solve()` method), `Assembly::JointGroup`.
* **Python modules in `Mod/Assembly`:** `JointObject` (`Joint`, `GroundedJoint`, `JointTypes`) and `UtilsAssembly`. `JointObject` imports `pivy.coin` and `SoSwitchMarker` unconditionally; `FreeCADGui`, `PySide.QtGui` and `QtWidgets` only under `if App.GuiUp:`.
* **GUI-only:** `ViewProviderJoint`, `TaskAssemblyCreateJoint`, selection-driven commands.
* **Rule:** under `FreeCADCmd`, do not import `FreeCADGui` or call `Gui.runCommand()`. Make sure `pivy` is importable in the headless environment **[VERIFIED 1.1.4 for the Windows installer]**: `pivy` and `pivy.coin` are bundled and import under `freecadcmd`. Conda and AppImage packagings are **[N/A]** (not pinned).
* `assembly.solve()` is called automatically when a joint is created, if the `SolveInJointCreation` preference is on (default).

---

### 5. Solver Engine (OndselSolver)

* **Solver backend:** OndselSolver, an open-source multibody kinematics solver.
* **License:** LGPL-2.1 **[VERIFIED]**: the `LICENSE` file of `github.com/FreeCAD/OndselSolver` (`main`, checked 2026-10-09) is the GNU LGPL version 2.1. The Windows installer does not ship a separate copy.
* **Headless:** a C++ library inside the FreeCAD core with no OpenGL or event-loop dependency, per the original report.

---

### 6. Known Assembly Bugs & Limitations

From the original report; checked in Phase 0 (S1–S4, S10):

1. **Recompute hangs:** recomputing complex assemblies with circular references can stall without raising a Python exception. **[FALSE → fallback]** as stated: cycles are detected, not hung on (object-level `setExpression` raises "creates a cyclic dependency"; spreadsheet cycles mark the sheet Invalid). Any real hang inside `recompute()` is handled by running every request in a subprocess with a timeout and a process-tree kill (S10, verified).
2. **No single-step solver API from Python**; the solve runs through `assembly.solve()` or a document recompute. **[VERIFIED 1.1.4]**: `assembly.solve()` returns 0 on success, but 0 does not prove the parts mated (see §1).
3. **Nested `App::Link` transform propagation** may need an extra recompute. **[N/A]**: the plan does not use `App::Link`.

---

### 7. Exporting Solved Assembly Poses (URDF / STEP Pipeline) **[CORRECTED]**

```python
import Part

# Export each part with its solved placement applied
for obj in assembly.Group:
    if hasattr(obj, "Shape") and not obj.isDerivedFrom("Assembly::JointGroup"):
        shape = obj.Shape.copy()
        shape.Placement = obj.getGlobalPlacement()
        shape.exportStep(f"/tmp/{obj.Label}_solved.step")

# Extract the joint tree
joint_records = []
for j in joints.Group:
    if not hasattr(j, "JointType"):            # skips the GroundedJoint
        continue
    parent = j.Reference1[0]
    child = j.Reference2[0]
    joint_records.append({
        "type": j.JointType,
        "parent": parent.Label,
        "child": child.Label,
        # Joint frames in each part's local coordinates
        "frame_in_parent": j.Placement1,
        "frame_in_child": j.Placement2,
    })
```

Corrections:
- Joints live in the `Assembly::JointGroup`, not loose in `doc.Objects`, and they are `App::FeaturePython`, so `isDerivedFrom("Assembly::JointObject")` never matches.
- The original computed the joint origin as `Reference1[0].Placement.inverse() * Reference2[0].Placement`. That is the relative pose of the two *parts*, not of the joint frames. For URDF, the joint origin is the joint frame expressed in the parent link frame.
- **[VERIFIED 1.1.4]** (S4) `Placement1`/`Placement2` are neither part-local nor global: they are expressed in the frame of the **referenced sub-object** (the LCS, or the feature for face/vertex refs). World frame = `UtilsAssembly.getGlobalPlacement(ref) * joint.PlacementN`. For whole-LCS refs `PlacementN` is identity, so the URDF joint origin is the parent LCS placement in the parent link frame.

---

### 8. Alternative Assembly Workbenches

| Workbench | Status (1.x) | License | Headless | Recommendation |
| :--- | :--- | :--- | :--- | :--- |
| **Built-in Assembly** | Mainline | LGPL-2.1 | Yes, with the caveats above | **Recommended** |
| **Assembly3** | Add-on | LGPL-2.1 [N/A] | Needs its solver build | Avoid for new agent pipelines |
| **Assembly4** | Add-on | LGPL-2.1 [N/A] | Expression-based placement, no kinematic solver | Only for pure placement |

---

## TOPIC B: Sketcher Constraint Diagnostics & Parameter Binding

### Executive Summary & Verdict
* **Verdict:** **Scriptable headless as-is** (unchanged).
* **Summary:** `Sketcher::SketchObject` exposes the solver result, degrees of freedom, and lists of conflicting, redundant, partially redundant and malformed constraints to Python. **[CORRECTED]** Some names in the original report were wrong: DoF is an attribute (`DoF`), not a method `getDoF()`, and there is no `getUnderConstrainedElements()`.

---

### 1. Constraint Status & Diagnostics **[CORRECTED]**

```python
import FreeCAD as App
import Part
import Sketcher

doc = App.newDocument("SketchDiagnostics")
sketch = doc.addObject("Sketcher::SketchObject", "AgentSketch")

sketch.addGeometry(Part.LineSegment(App.Vector(0,0,0),   App.Vector(10,0,0)),  False)
sketch.addGeometry(Part.LineSegment(App.Vector(10,0,0),  App.Vector(10,10,0)), False)
sketch.addGeometry(Part.LineSegment(App.Vector(10,10,0), App.Vector(0,10,0)),  False)
sketch.addGeometry(Part.LineSegment(App.Vector(0,10,0),  App.Vector(0,0,0)),   False)

sketch.addConstraint(Sketcher.Constraint('Coincident', 0, 2, 1, 1))
sketch.addConstraint(Sketcher.Constraint('Coincident', 1, 2, 2, 1))
sketch.addConstraint(Sketcher.Constraint('Coincident', 2, 2, 3, 1))
sketch.addConstraint(Sketcher.Constraint('Coincident', 3, 2, 0, 1))

rc = sketch.solve()
# 0 = success. Otherwise, in priority order:
# -4 over-constrained, -3 conflicting, -5 malformed, -1 solver error, -2 redundant
# [VERIFIED 1.1.4, S6]: a conflicting sketch returned -4 (DoF -1), a redundant one -2

dof = sketch.DoF                                   # int attribute (NOT getDoF())
fully = sketch.FullyConstrained                    # App::PropertyBool on the object
conflicting = sketch.ConflictingConstraints        # list of constraint indices
redundant = sketch.RedundantConstraints
partially_redundant = sketch.PartiallyRedundantConstraints
malformed = sketch.MalformedConstraints
```

Indexing **[VERIFIED 1.1.4, S6]**: the four lists are **1-based** (the 12th constraint is reported as `12`). Use `sketch.Constraints[i - 1]`. In the conflicting case the list holds every involved constraint (8 of 12), so treat it as a candidate set. A sketch passes only if `solve() == 0 and FullyConstrained` (a redundant sketch has `DoF == 0` but `FullyConstrained == False`).

---

### 2. Finding Under-Constrained Geometry **[CORRECTED]**

`getUnderConstrainedElements()` does not exist. The closest API is:

```python
free = sketch.getGeometryWithDependentParameters()   # list of (geoId, posId) pairs
for geo_id, pos_id in free:
    print(geo_id, pos_id, type(sketch.Geometry[geo_id]).__name__)
```

`posId` identifies the point: 0 = whole edge, 1 = start, 2 = end, 3 = centre, by Sketcher convention. **[VERIFIED 1.1.4, S6]**: returns `(geoId, posId)` pairs (0-based `geoId`) for an under-constrained sketch, possibly with repeats, and `[]` for a fully constrained one. Whether they match the GUI highlighting is **[N/A]** headless.

---

### 3. Binding Constraints to Spreadsheet Parameters **[PARTLY CORRECTED]**

```python
sheet = doc.addObject("Spreadsheet::Sheet", "Params")
sheet.set("B1", "=50 mm")                # write "=<value> <unit>": "-7 mm" (no "=") is stored as text  [VERIFIED 1.1.4, S7]
sheet.setAlias("B1", "BoxWidth")
doc.recompute()

idx = sketch.addConstraint(Sketcher.Constraint('DistanceX', 0, 1, 0, 2, 10.0))
sketch.renameConstraint(idx, "box_width")        # named constraints make expressions readable

# Bind the constraint to the alias. Path syntax  [VERIFIED 1.1.4, S5]: '.Constraints.box_width',
#   'Constraints.box_width', '.Constraints[<idx>]', 'Constraints[<idx>]' all work; stored as '.Constraints.box_width'
sketch.setExpression('.Constraints.box_width', 'Params.BoxWidth')
doc.recompute()

print(sketch.getDatum("box_width"))      # Quantity; getDatum/setDatum accept an index or a name
```

Notes:
- `setExpression` is a generic document-object method, not a Sketcher-specific one; its path must match an existing constraint. If one form fails, inspect `sketch.ExpressionEngine` after binding one constraint in the GUI to see the exact path format.
- `getConstraintValue()` **[FALSE → fallback]**: it does not exist in 1.1.4 (`AttributeError`). Use `getDatum(name_or_index)`, **[VERIFIED 1.1.4]**.
- Alias names: letters, digits and underscores, starting with a letter; names that look like cell addresses (e.g. `AB123`) are rejected. **[VERIFIED 1.1.4, S7]**: unit symbols are rejected too (`h`, `m`, `mm`, `s`, `g`, `deg` raise `ValueError: Invalid alias`); `len`, `L`, `width`, `x`, `a1` are accepted.
- Changing the cell + `doc.recompute()` moves the geometry, also after save + reopen **[VERIFIED 1.1.4, S5]**.

---

### 4. Auto-Constraint Helper **[CORRECTED]**

* The method is **`sketch.autoconstraint(precision, angle_precision, include_construction=True)`** (all lowercase; **[VERIFIED 1.1.4]**, `autoConstraint` does not exist), not `autoConstraint()`. It adds coincident and horizontal/vertical constraints based on proximity tolerances.
* Use it only as a first pass, then re-check `ConflictingConstraints` and `RedundantConstraints`.

---

### 5. Topological Naming Problem (TNP) & Mitigation in 1.x

FreeCAD 1.0 merged a topological-naming mitigation (originally from the realthunder branch). Face references such as `Pad.Face6` can still break when an edit changes the topology (e.g. a rectangle becomes a hexagon).

**Mitigation:** attach sketches to datum planes or LCS objects defined from parameters, not to solid faces:

```python
datum_plane = body.newObject("PartDesign::Plane", "DatumPlane")  # inside a PartDesign::Body
datum_plane.AttachmentSupport = [(body.Origin.OriginFeatures[3], "")]  # the body's XY_Plane
datum_plane.MapMode = "FlatFace"
datum_plane.setExpression(".AttachmentOffset.Base.z", "Params.top_z")  # [VERIFIED 1.1.4, S7]

sketch.AttachmentSupport = [(datum_plane, "")]   # property renamed from 'Support' in 1.0
sketch.MapMode = "FlatFace"
```

**[FALSE → fallback]**: the original snippet set `MapMode = "Translate"` with no `AttachmentSupport`. The pattern above (support = body XY_Plane, `FlatFace`, offset bound to a parameter) is **[VERIFIED 1.1.4]** for `PartDesign::Plane` and `PartDesign::CoordinateSystem` (S7). It survives save + reopen, and a sketch and Pad on the datum follow the parameter. S8 uses it for "sketch on the top face" with no face references.

---

### 6. Error Detection

The original report used `sketch.hasErrors()` and `sketch.ErrorStatus`. **[FALSE → fallback]**: neither exists in 1.1.4. The fallback below is **[VERIFIED 1.1.4]** (S6–S10); `obj.getStatusString()` gives the message:

```python
doc.recompute()
for obj in doc.Objects:
    if not obj.isValid() or "Invalid" in obj.State:
        print("Object in error state:", obj.Label, obj.State)
```

For sketches, prefer the explicit solver diagnostics in §B1 over a generic error flag.

Also verified (S10):
- `doc.recompute()` returns the number of recomputed objects, **not** a status.
- Some degenerate inputs raise no error at all: a self-intersecting profile pads into 2 valid solids, and a Pocket that removes everything leaves 0 solids. Gate on `len(body.Shape.Solids) == 1` and `Volume > 0`, not only on `State`.

---

## FreeCAD Version Compatibility Matrix **[CORRECTED]**

| Feature / API | FreeCAD 0.21 | FreeCAD 1.0 | FreeCAD 1.1+ |
| :--- | :--- | :--- | :--- |
| Integrated Assembly workbench | ❌ (add-ons only) | ✅ Native | ✅ Native |
| OndselSolver | ❌ | ✅ | ✅ |
| TNP mitigation | ❌ | ✅ | ✅ |
| `sketch.solve()` return codes | ✅ | ✅ | ✅ |
| `sketch.DoF`, `ConflictingConstraints`, `RedundantConstraints` | not tested | not tested | ✅ verified 1.1.4 |
| `PartiallyRedundantConstraints`, `MalformedConstraints`, `FullyConstrained` | not tested | not tested | ✅ verified 1.1.4 |
| `getGeometryWithDependentParameters()` | not tested | not tested | ✅ verified 1.1.4 |
| Constraint expressions (`setExpression`) | ✅ | ✅ | ✅ (`.Constraints.<name>`, verified 1.1.4) |
| `AttachmentSupport` (renamed from `Support`) | ❌ | ✅ | ✅ |

The original matrix listed `Sketcher.getDoF()` and `getUnderConstrainedElements()`, which don't exist under those names. Only the pinned release (1.1.4) was tested; the 0.21 and 1.0 columns are **[N/A]** for this project.

---

## Sources & Verification Status

### Verified against source (official FreeCAD repository, `main` branch, 2026-10-09)
1. `src/Mod/Assembly/JointObject.py`: `Joint(joint, type_index)`, `GroundedJoint(joint, obj)`, `JointTypes`, reference format, `Placement1/2`, `Offset1/2`, `Detach1/2`, `Distance`, `Distance2`, `Angle`, limit property names, GUI import behaviour.
2. `src/Mod/Assembly/AssemblyTests/TestCore.py`: `Assembly::AssemblyObject` + `Assembly::JointGroup`, joints as `App::FeaturePython`, `setJointConnectors`, placement checks with `isSame(..., 1e-6)`.
3. `src/Mod/Sketcher/App/SketchObject.pyi`: `solve()` return codes, `DoF`, `ConflictingConstraints`, `RedundantConstraints`, `PartiallyRedundantConstraints`, `MalformedConstraints`, `getGeometryWithDependentParameters()`, `autoconstraint()`, `renameConstraint()`, `setDatum()`, `getDatum()`.
4. `src/Mod/Sketcher/App/SketchObject.h`: `FullyConstrained` property.

### Still unverified
1. Headless import of `JointObject` in each FreeCAD packaging (depends on `pivy`).
2. Referencing an LCS inside an `App::Part` as a joint reference.
3. How to drive a revolute or slider joint to a given value (likely via `Offset2`).
4. The frame of `Placement1`/`Placement2` (local vs. global).
5. Constraint expression path syntax (`.Constraints.<name>` vs. `Constraints[<i>]`).
6. OndselSolver license file for the pinned version.
7. The known-bug list in §A6 and the memory-leak claim from the original report.

---

## Top 5 Automated Pipeline Risks & Mitigations

| Risk | Impact | Mitigation |
| :--- | :--- | :--- |
| **1. Recompute hangs** | Process stalls indefinitely | Run every `doc.recompute()` / `assembly.solve()` in a subprocess with a hard timeout |
| **2. Face index shifting (TNP)** | Downstream features fail when topology changes | Attach sketches to datum planes / LCS, never to solid faces |
| **3. Assembly over-specification** | Solver fails or leaves parts unmoved | Kinematic tree only; check that each part moved as expected after solving (compare placements with a tolerance, as the official tests do) |
| **4. Unit mismatches in expressions** | Constraints scaled 1000× (mm vs. m) | Store spreadsheet values with explicit units (`"50 mm"`) |
| **5. Stale shape handles** | Crashes or wrong geometry from cached C++ objects | Re-read `obj.Shape` after every recompute instead of caching it |
