# P0 spike results (FreeCAD 1.1.4, freecadcmd, GuiUp = 0)

P1 reads this file instead of re-running spikes. Rerun with `uv run python -m spikes.run s01 s02 ...`;
raw results land in `spikes/results/<id>.json`.

| Spike | Verdict | One-line answer |
|---|---|---|
| S1 | PASS | Assembly WB joints create and solve headless; `assembly.solve()` returns 0 |
| S2 | PASS | Reference an LCS as `[part, ["LCS_x.", "LCS_x."]]`; **trailing dot is required** |
| S3 | PASS (single joint) | `joint.Offset1 = Rz(q)` drives a revolute to +q; limits min=max are ignored |
| S4 | PASS | `PlacementN` is local to the referenced sub-object; chains need own-FK pre-placement |
| S5 | PASS | `sk.setExpression(".Constraints.<name>", "Params.<alias>")`; survives save/reopen |
| S6 | PASS | 4 sketch cases distinguishable by `solve()`, `DoF`, lists; **conflict/redundant indices are 1-based** |
| S7 | PASS | Datum `.AttachmentOffset.Base.z` ← `Params.x` works; **cells must be written `"=<v> <unit>"`**; unit-name aliases rejected |
| S8 | PASS | Params → sketch → Pad → datum → Pocket → FCStd/STEP/STL; tree + expressions survive reopen |
| S9 | PASS | mm / mm³ / mm⁵, density 1; feature `.Shape` excludes parent `App::Part` placement; inertia about CoM |
| S10 | PASS | hang → timeout kill; segfault → "no response"; 2 degenerate inputs are **silent** ⇒ gate on `n_solids == 1` |
| S11 | PASS (Agent SDK live) · API backend offline only | One `ToolSpec` list runs on both backends; built-ins off; writes outside `parts/<own>.py` denied |
| S13 (P2) | PASS (insert-after) | Fillet/Chamfer edge refs survive size edits and reopen; break (`?EdgeN`, Invalid, stale shape kept) when a later feature splits the edge ⇒ insert the dress-up right after the feature it names |
| S14 (P2) | PASS | Mirrored / LinearPattern / PolarPattern / Thickness headless with Length, Occurrences, Angle, Value bound to Params; **a Transformed made by `newObject` does not become the Tip** |

## S1 — Assembly WB headless
- `import JointObject, UtilsAssembly` works in freecadcmd; `pivy` is bundled in the Windows installer.
- Joints: `joints.newObject("App::FeaturePython", name)` + `JointObject.Joint(obj, JointTypes.index("Revolute"))`; ground with `JointObject.GroundedJoint(obj, part)`.
- `JointTypes` (1.1.4): Fixed, Revolute, Cylindrical, Slider, Ball, Distance, Parallel, Perpendicular, Angle, RackPinion, Screw, Gears, Belt.
- Fallback (b) (own SE(3) aligner, no WB joints) is **not** needed.

## S2 — LCS as joint reference
- `App::LocalCoordinateSystem` created with `part.newObject(...)` inside an `App::Part`.
- Sub-name `"LCS_x."` (whole LCS, empty element) ⇒ `findPlacement` returns identity ⇒ joint frame = LCS frame.
- Sub-name `"LCS_x"` (no dot) is **silently wrong**: `solve()` returns 0 but nothing mates.
  ⇒ the SDK must check frame coincidence after every solve; never trust the return code alone.
- Both Fixed (full frame incl. x) and Revolute (origin + z; x also matched by pre-solve) coincide to ~1e-14 mm.

## S3 — driving a revolute
- `joint.Offset1 = Placement(0, Rotation(Z, q_deg))` then `assembly.solve()` → child LCS = parent LCS · Rz(q), error ~1e-14.
- `Offset2 = Rz(-q)` is equivalent. `EnableAngleMin/Max` + `AngleMin = AngleMax = q` does **not** drive (solve 0, pose unchanged).
- Setting `Offset1/2` triggers `preSolve` → `matchJCS` → `solve` from `onChanged`.

## S4 — frames, and chains
- `PlacementN` is expressed in the frame of the **referenced sub-object** (the LCS, or the Box for face refs), not the part and not global.
  Helper: `world = UtilsAssembly.getGlobalPlacement(ref) * joint.PlacementN`.
- **Chain hazard:** with ≥ 2 revolutes, Offset-only driving lands the distal link 180° off in 3 of 7 poses (solve 0, positions exact).
  Cause: `matchJCS` picks "matched or flipped, whichever is closer" from the current pose, and the solver keeps that branch.
- **Chosen method ("preplace")**, 0 error on all poses:
  1. compute every part placement with our own FK (P0 §4 mating rule, composed root → leaf);
  2. write `part.Placement` and `joint.Offset1 = Rz(q)` for every joint;
  3. `assembly.solve()`; then check LCS frame coincidence (residual gate).
  Own FK is the pose authority; the WB joints keep the `.FCStd` editable and act as a consistency check.

## S5 — sketch constraint ↔ spreadsheet
- `sheet.set("B1", "50 mm"); sheet.setAlias("B1", "upper_arm_len")`
- `idx = sk.addConstraint(...); sk.renameConstraint(idx, "len")`
- `sk.setExpression(".Constraints.len", "Params.upper_arm_len")`. All of `.Constraints.len`, `Constraints.len`,
  `.Constraints[idx]`, `Constraints[idx]` work and are stored as `.Constraints.len`.
- Changing the cell + `doc.recompute()` changes `getDatum("len")` and the Pad volume; still true after `saveAs` + `openDocument`.
- Live parameters are kept; the "set datums from Python" fallback is not needed.

## S6 — sketch diagnostics (L2 gate input)
Rectangle + case constraints; `sk.solve()` then read properties.

| Case | `solve()` | `DoF` | `FullyConstrained` | lists | `getGeometryWithDependentParameters()` | `State` |
|---|---|---|---|---|---|---|
| fully constrained | 0 | 0 | True | all empty | `[]` | Up-to-date |
| under (1 DoF) | 0 | 1 | False | all empty | `[(geo, pos), ...]` (pos 1=start, 2=end, 3=centre) | Up-to-date |
| conflicting | -4 | -1 | False | `ConflictingConstraints` = 8 indices (broad: all involved) | `[]` | Touched, Invalid |
| redundant | -2 | 0 | False | `RedundantConstraints = [12]` | `[]` | Touched, Invalid |

- **Indices in the 4 lists are 1-based** (12 constraints, redundant one reported as 12). Convert with `i - 1` before `sk.Constraints[i]`.
- `getGeometryWithDependentParameters()` returns 0-based geometry indices with a point position id; may repeat entries.
- `getStatusString()` gives a human message ("Over-constrained sketch ... Remove at least one of ...").
- Gate rule: pass iff `solve()==0 and FullyConstrained`; `DoF` tells under (>0) vs. over (<0); conflicting list is a *candidate set*, not the culprit.
- `PartiallyRedundantConstraints` / `MalformedConstraints` exist and were empty in all cases.

## S7 — datum attachment bound to Params
- `PartDesign::Plane` and `PartDesign::CoordinateSystem` in a Body: `AttachmentSupport = [(XY_Plane, "")]`, `MapMode = "FlatFace"`,
  `setExpression(".AttachmentOffset.Base.z", "Params.<alias>")` (also without the leading dot). A sketch on the datum and its Pad follow the param; survives save/reopen.
- **Spreadsheet values:** `sheet.set(cell, "-7 mm")` is stored as *text* (`'-7 mm`), and the bound property goes Invalid
  ("Failed to convert to Quantity"). Always write `"=<value> <unit>"` (e.g. `"=-7 mm"`). Plain positive `"12.5 mm"` happens to work.
- **Aliases that are unit symbols are rejected** by `setAlias` (`ValueError: Invalid alias`): `h, m, mm, s, g, deg` failed;
  `len, L, width, x, a1, upper_arm_len` ok. ⇒ the SDK validates param names (snake_case, ≥ 2 words or a known-safe list) and reports a clear error.

## S8 — full single-part pipeline
`spikes/s08_single_part_pipeline.py`: Params(plate_w, plate_d, thick, hole_d) → fully constrained plate sketch on XY_Plane → `Pad.Length ← Params.thick`
→ `PartDesign::Plane` at `z ← Params.thick` → circle sketch (diameter ← `Params.hole_d`) → Pocket ThroughAll.
- Volume exact vs. analytic before and after a param change; after reopen the feature tree and all `ExpressionEngine` entries are identical.
- `body.Shape.exportStep(fn)`; `Part.read(fn).Volume` equals the body volume exactly.
- STL: `MeshPart.meshFromShape(Shape=..., LinearDeflection=0.05, AngularDeflection=0.2, Relative=False)` → closed mesh, rel. volume error 9e-5, 268 facets.
- Datum planes bound to params (not face references) avoid topological naming entirely; recommended SDK pattern for "sketch on top face".

## S9 — mass properties
Box 10×20×30 mm. `shape.Volume` mm³; `Solid.CenterOfMass` mm; `Solid.MatrixOfInertia` mm⁵ (density 1, about the CoM);
`Solid.Mass` = volume (no density); `PrincipalProperties` available.

| Shape | CoM / inertia axes are in |
|---|---|
| `Part.makeBox` with `shape.Placement` | the placed frame (includes the shape's Placement) |
| `Part::Box` feature inside a placed `App::Part` | the feature's placement only — **parent `App::Part` placement not applied** |
| `App::Part.Shape` | global (includes the container placement) |
| `PartDesign::Body.Shape` inside an `App::Part` | Body placement only (same as above) |

⇒ For URDF inertials: compute on the link's own shape in the link frame; with ρ in kg/mm³:
mass [kg] = ρ·V; CoM [m] = CoM [mm]·1e-3; inertia [kg·m²] = ρ·I [mm⁵]·1e-6.
`obj.getGlobalPlacement()` gives the full chain when global values are needed.

## S10 — hang / crash
`spikes/s10_hang_crash.py` + orchestrator driver `spikes/drivers/s10.py`.
- Hang inside `doc.recompute()` (FeaturePython `execute` loops): killed at timeout (10.2 s for a 10 s budget), harness `failure` "timeout ... worker killed", no orphan processes.
- Real segfault (`faulthandler._sigsegv()`): worker dies in 0.4 s, **exit code still 0**; harness `failure` "worker exited ... without a response".
  (A ctypes access violation is *not* a crash on Windows: Python raises `OSError`, caught by the worker as a normal failure.)
- Clean failures (object Invalid + message): cyclic spreadsheet refs (sheet Invalid), object-level cycle (`setExpression` raises "creates a cyclic dependency"),
  Pad length 0, open wire ("Wire is not closed."), empty sketch.
- **Silent**: self-intersecting (bow-tie) profile pads into 2 solids, valid, no error; a Pocket that removes everything leaves 0 solids, volume 0, no error.
  ⇒ L2/L3 gates must check `len(body.Shape.Solids) == 1` and volume > 0, not only `State`.
- `doc.recompute()` returns the number of recomputed objects, not a status; scan `o.State` for `Invalid`/`Error` instead.

## S11 — LLM backends (T0.8)
Code: `llm/tools.py` (ToolSpec + `PartWorkspace`), `llm/backend.py` (`LLMBackend`, `RunResult`),
`llm/agent_sdk_backend.py`, `llm/claude_api_backend.py`; live spike `spikes/s11_llm.py`; offline tests `tests/test_llm_permissions.py`.
- Tools are defined once (`ToolSpec`: name, description, JSON schema, function). The **path policy lives in `PartWorkspace`**, not in a backend,
  so both backends enforce the same rule: `write_file` accepts only `parts/<own>.py` (traversal, absolute paths elsewhere, other parts and
  other files are denied); `read_file` only the own file and the read-only roots.
- **Agent SDK option names (claude-agent-sdk 0.2.165)** — the restriction layers:
  `tools=[]` (no built-in tools), in-process MCP server via `create_sdk_mcp_server` + `@tool` (tools appear as `mcp__agenticcad__<name>`),
  `allowed_tools=[those names]`, `permission_mode="dontAsk"`, `disallowed_tools=[Bash, Write, Edit, Web*, Agent, ...]`,
  `PreToolUse` hook that denies anything else, `setting_sources=[]`, `strict_mcp_config=True`, `skills=[]`, `max_budget_usd`.
  `can_use_tool` is **not** used: it only fires when rules evaluate to "ask", and `dontAsk` never asks.
- **Live Agent SDK run** (Sonnet 5.5, subscription login, 4 turns, $0.014): wrote `parts/s11_box.py`, `compile_part` ran it in FreeCAD 1.1.4
  (volume 6000), the write to `parts/forearm.py` came back as a denied tool error, and the model reported it had no shell tool.
- **ClaudeAPIBackend** uses `client.beta.messages.tool_runner` with `BetaFunctionTool(fn, name=, description=, input_schema=)`;
  a denied call raises `ToolError` so the model gets `is_error: true`. Tested **offline** against a scripted Messages API
  (`httpx2.MockTransport`): tool loop, denial and compile all correct. **Live run pending an `ANTHROPIC_API_KEY`** (none on this machine).

## T0.7 — 1-joint URDF package
`spikes/urdf_1joint/` (ament_cmake package): `urdf/one_joint.urdf`, `meshes/*.stl` (metres), `make_meshes.py` (worker: meshes + inertials), `check.py`.
- `base_link` = MG996R card frame; `arm_link` = horn connector frame; joint origin = card `output_shaft` (10, 0, 16.9) mm; `axis 0 0 1`;
  limits from the card (±90°, effort 1.08 × 0.5 = 0.54 N·m, velocity 7 rad/s); inertials per S9 (arm checked against m/12·(b²+c²)).
- Results (`spikes/results/t07.json`), Docker image `agenticcad-ros:jazzy` (`docker/ros-tools/Dockerfile`: ROS 2 Jazzy + Gazebo Harmonic + pinocchio): **all four pass**.
  yourdfpy (Windows venv): FK at q = 0.5 exact, both meshes load. check_urdf: parsed, root base_link → arm_link. `gz sdf -p`: SDF emitted.
  pinocchio: nq 1, effort 0.54, velocity 7.0, link masses 0.055 / 0.003048, FK error 0. (WSL Ubuntu-22.04 + Humble also passes check_urdf: `AGENTICCAD_LINUX=wsl`.)

## S13 — dress-up edge references (T2.1, 2026-10-10)
- FreeCAD 1.1 stores `Base` sub-names as mapped element names; size edits and save/reopen re-resolve them.
- A later feature that splits a referenced edge (notch moved across it) ⇒ `?Edge10`, "Invalid edge link", the
  feature keeps its old shape (volume looks fine: only `State` tells). Undoing the edit recovers.
- Same plate with the dress-up inserted right after the pad (`body.Tip = pad`, `newObject`, Tip back): all edits pass.
- Follow-up probe: **edges along a pad (generated from sketch vertices, mapped `#f:1;:G;XTR`) are renamed on every
  recompute** (string-table id changes); cap edges (`#b:1;:H…`, `#16:1;:U;XTR`) are stable. ⇒ SDK offers cap
  edges only (`start`/`end`/`caps`); vertical edges are rounded in the sketch (`rect(..., r=)`). D45.
- `PartDesign::AddSubShape` of a pad is in the body frame (identity placement).

## S14 — patterns, mirror, shell (T2.1, 2026-10-10)
- `Mirrored.MirrorPlane = (origin plane | datum, [""])`, `Originals = [features]`; `TransformMode` = "Features".
- `LinearPattern`: `Direction = (X_Axis, [""])`, `Length` (overall) ← `Params.spacing * (Params.count - 1)`,
  `Occurrences` ← unitless cell `=3`. `PolarPattern`: `Axis`, `Angle` (360 = evenly round; else both ends
  included), `Occurrences`. Counts edit and survive reopen.
- **`body.newObject` does not move the Tip to a Transformed feature** (Originals empty at creation): set `body.Tip`.
- `Thickness`: `Base = (feature, [faces])`, `Value` bound; default is inward, faces removed are open.
