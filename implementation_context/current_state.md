# CURRENT STATE — read this first

```yaml
updated: 2026-10-10
plan_version: v0.4 (IMPLEMENTATION_PLAN.md)
current_phase: P2
current_phase_status: in_progress; T2.1, T2.2 done (split moved to T2.4). P1 DONE 2026-10-10
next_action: T2.3 L5 sweep (9/joint, ≤125 poses, LHS > 3 joints, min clearance per pair) + run_tests helpers (§3.2). Watch: L4 adds up to ~30 s per passing compile on big parts (golden base 31 s); the CODE→DESIGN escalation (D43) is offline-tested only; O13 cosmetic
code_written: harness/ (worker protocol + tests), spikes/run.py, spikes/s01-s10, spikes/drivers/s10.py, components/ (card.py with card expressions, build.py, cards: servo/mg996r (+ horn), electronics/generic_bay, esp32_devkit, pca9685, fastener/m3_screw, m3_insert; servo_mg996r generator), tests/test_components.py, llm/ (tools, backend, agent_sdk_backend, claude_api_backend), spikes/s11_llm.py, spikes/urdf_1joint/, tests/test_llm_permissions.py, schemas/ (plan, signal, profile, names, export → schemas/json/), profiles/ (generic_fdm, materials), tests/test_schemas.py, sdk/ (expr, sketch, part, errors), tests/test_sdk.py + tests/freecad/sdk_cases.py, docs/sdk.md, harness/ (lint L0, part_ops worker ops, tools compile_part/probe_part, feedback), tests/test_harness_tools.py, assembly/ (kinematics, writer, builder), tests/test_assembly.py + tests/freecad/assembly_reopen.py, assembly/owners.py, validate/ (poses, statics, masses, plan_check, sweep worker ops, assembly_check, printability), tests/test_validate.py, export/urdf.py, description/checks.py, tests/test_export.py, packs/ (loader; manipulator: composites.py servo_u_bracket, knowledge.md, bench.yaml B1-1..5), tests/test_packs.py, harness/pipeline.py (run_job: plan → parts → L5/L6 → L7 → build → export → D1/D2/D4), bench/golden/b1_3/ (plan.json + parts base/turret/arm), tests/test_golden.py + tests/freecad/golden_edit.py, SDK v1 core calls (sketch.polyline, rect r=, fillet/chamfer/shell by feature cap class, mirror, pattern_linear/polar, boss, rib), spikes s13/s14, bearing cards (608zz, 623zz), manipulator composites bearing_seat / horn_coupler / link_bar / hollow_link / turntable_base, validate/flex.py (L4 in compile_part, router L4 rules, flex on 16 SDK v1 samples) (199 tests total)
git: branch p0-env-spikes, pushed to origin (GitHub auth works from 2026-10-10); latest 8ec7db1 + T2.1 commit
```

## 1. How to use this directory (token-saving protocol)
1. Load **this file** + the **current phase file** only.
2. Open `IMPLEMENTATION_PLAN.md` only at the `§` cited in the phase file's `plan_refs`, and only when the phase file isn't enough.
3. Open `REFERECES/` files only via `REFERECES/REFERENCE_GUIDE.md`.
4. After any work session: update §2, §4, §5, §6 of this file (keep it < 150 lines). Phase files change only if scope or specs change; log that in §5.

## 2. Phase status

```mermaid
flowchart LR
    P0["P0 env + spikes<br/>DONE"] --> P1["P1 2-DOF e2e<br/>DONE"] --> P2["P2 3-DOF + validation"]:::cur --> P3["P3 wrist · gripper · ROS 2"] --> P4["P4 6-DOF · memory · edits"] --> P5["P5 product hardening"]
    classDef cur fill:#ffd54f,stroke:#e65100,color:#000
```

| Phase | File | Status | Exit met |
|---|---|---|---|
| P0 | `phase_0_environment_and_spikes.md` | done | yes (O11 open) |
| P1 | `phase_1_2dof_end_to_end.md` | done | yes 2026-10-10 |
| P2 | `phase_2_3dof_validation_depth.md` | in_progress | — |
| P3 | `phase_3_wrist_gripper_ros2.md` | not_started | — |
| P4 | `phase_4_6dof_memory_edits.md` | not_started | — |
| P5 | `phase_5_product_hardening.md` | not_started | — |

## 3. Environment facts (verified 2026-10-09)

| Item | Value | Status |
|---|---|---|
| OS | Windows 11 Home 10.0.26200 | ok |
| FreeCAD | **1.1.4** (build 20260928), `C:/Program Files/FreeCAD 1.1/bin/freecadcmd.exe` | ok, `freecadcmd -c` runs |
| FreeCAD Python | 3.11.14 (bundled) | ok |
| System Python | 3.14 at `C:/Python314` (conda: none, not needed) | **don't use** for orchestrator |
| uv | 0.12.24 | ok → orchestrator venv (Python 3.12) |
| Docker | 29.4.0, Docker Desktop running 2026-10-09; image `agenticcad-ros:jazzy` present | ok |
| Assembly / pivy import headless | `JointObject`, `UtilsAssembly`, `pivy` import; joints solve | ok (S1) |
| Orchestrator venv | uv-managed CPython 3.12.15, `python-preference = only-managed` (LibreOffice 3.12 on PATH breaks venvs) | ok |
| WSL | default distro `docker-desktop` (no bash) ⇒ always `wsl -d <distro>`; Ubuntu-22.04 has ROS 2 Humble (`check_urdf`), no gz / pinocchio; Ubuntu 26.04 bare; sudo needs a password | Humble fallback only |
| LLM auth | Claude Code subscription login present; no `ANTHROPIC_API_KEY` (not needed before P5) | Agent SDK ok; API backend offline (O11, P5) |
| pinocchio | no Windows wheel (`cmeel-assimp`) | → Docker image (D24) |

## 4. Task board (current phase: P2)

P1 board (T1.1–T1.12, all done) is in git at 8ec7db1. P1 data mined 2026-10-10 (13 jobs, 126 compiles, 37 failed: sdk 28, L1 7, L3 2; DoF-0 1.0; assembly rounds lost only to L5 overlaps): top SDK misuse = redefining a plan param in `part.params` (13), reserved / non-snake part names (fixed in P1), unit-symbol names `w`/`t`/`bar` (5). Agents use only sketch/rect/pad/datum/connector/pocket/hole/mount; no raw FreeCAD, no fillet/chamfer/shell (SDK lacks them).

| ID | Task | Status |
|---|---|---|
| T2.1 | SDK v1 + tests; fix top P1 misuse | done 2026-10-10: P1 misuse fixes; core calls (S13/S14, D45) `polyline`, `rect(r=)`, `fillet`/`chamfer`/`shell` (cap classes), `mirror`, `pattern_linear`/`polar`, `boss`, `rib`; cards `bearing/608zz`, `bearing/623zz`; manipulator composites `bearing_seat`, `horn_coupler`, `link_bar`, `hollow_link`, `turntable_base` (D46). `split` moved to T2.4 (its contract is the L7 build-volume route) |
| T2.2 | L4 flex test | done 2026-10-10: `validate/flex.py` inside worker `compile_part` after the nominal save (D47); golden B1-3 passes (base 27 runs / 31 s, turret 30, arm 9); router: L4 → CODE part, same plan param failing in ≥ 2 parts → DESIGN; 16 SDK v1 samples flex clean (3 fixtures fixed: stacked features on separate literals); `harness.tools.FLEX` off in e2e agent/bench tests |
| T2.3 | L5 sweep + `run_tests` helpers | — (top failing assembly gate in P1) |
| T2.4 | L7-full | — (+ `split(by=dovetail\|screws)` from T2.1: decide how the plan holds the two halves) |
| T2.5 | Renderer + `render_part`; L3v | — |
| T2.6 | L8 review | — |
| T2.7 | Router v2, escalation, budgets | — |
| T2.8 | Mechanisms pack v0 | — |
| T2.9 | 3-DOF golden | — |
| T2.10 | Bench v1 + 3 configs | — |
| T2.11 | Recalibrate plan-level L6 densities | — |

## 5. Decision log (do not re-litigate)

| # | Decision | Source |
|---|---|---|
| D1 | Kernel: FreeCAD 1.x (pinned 1.1.4) + OCCT; CadQuery/build123d not primary; OpenSCAD/SolveSpace rejected (GPL) | plan §2.1 |
| D2 | Agents write programs against our FreeCAD-native SDK (Articraft pattern); ArtiCAD connector contract + deterministic assembly | plan §3.1–3.3 |
| D3 | Core + domain packs; manipulator arm is the benchmark, not the scope | plan §1.4, user 2026-10-09 |
| D4 | LLM: Claude. Prototype via Agent SDK on user's subscription; product via Claude API + API key behind `LLMBackend` | plan §2.3 |
| D5 | Models: Opus 5.5 for requirements/design/review; Sonnet 5.5 for part agents (escalate to Opus) and router | plan §2.3 |
| D6 | Actuators: MG996R main, MG90S wrist, SG90 gripper; derating 0.5 | plan §3.6, §6.2 |
| D7 | Electronics: generic 75×60×25 mm bay unless user names boards | plan §3.6 |
| D8 | Printing: printer-agnostic generic FDM profile; PETG default, PLA allowed | plan §4.2, §6.2 Q13 |
| D9 | 6-DOF target: 250 mm reach, 50 g payload (stretch 100 g); end effector SG90 parallel gripper | plan §6.2 Q11–Q12 |
| D10 | No closed loops; no GUI round-trip; no simulation in loop (Gazebo/ROS 2 compatible output only) | user answers Q2–Q4 |
| D11 | URDF exporter is our own; CROSS/RobotCAD (LGPL-2.1) as reference only | plan §5.2 |
| D12 | Two Pythons: SDK/worker inside freecadcmd (3.11); orchestrator in uv venv (3.12); JSON over files between them | P0 §1 |
| D13 | Mating rule: connector frames coincide at q=0; `T_child(q)=T_parent·Cp·Rz(q)·Cc⁻¹` | P0 §4 |
| D14 | Keep Assembly WB joints (S1 PASS). Joint refs = `[part, ["LCS.", "LCS."]]` (trailing dot) | S1, S2 |
| D15 | Posing = "preplace": own FK writes part placements + `Offset1=Rz(q)`, then `solve()` + LCS-coincidence residual gate. Offset-only flips 180° in chains | S3, S4 |
| D16 | Params binding: `sk.setExpression(".Constraints.<name>", "Params.<alias>")` | S5 |
| D17 | L2 sketch gate: pass iff `solve()==0 and FullyConstrained`; conflict/redundant list indices are **1-based** | S6 |
| D18 | Params cells written as `"=<value> <unit>"`; SDK validates aliases (unit symbols like `h`, `m`, `s`, `g`, `deg` are rejected) | S7 |
| D19 | "Sketch on a face" = sketch on a `PartDesign::Plane` whose offset is bound to Params (no face refs, no TNP) | S7, S8 |
| D20 | Geometry gate checks `len(Solids)==1`, volume > 0 and object `State`; `recompute()` return value is not a status | S10 |
| D21 | Mass props: feature `.Shape` excludes parent `App::Part` placement; inertia about CoM, density 1, mm⁵ | S9 |
| D22 | Card frame = its mount connector (MG996R: flange underside, centred on holes, +z to shaft). Card `connectors`/`interfaces` name `params_mm` entries (`components/card.py: resolve`); generators bind sketches + LCS to the same Params | T0.6 |
| D23 | LLM action space: tools defined once as `ToolSpec`; path policy in `PartWorkspace`; Agent SDK hardened with `tools=[]`, MCP server tools only, `dontAsk`, PreToolUse deny hook, `setting_sources=[]`, `strict_mcp_config`, `skills=[]` | S11 |
| D25 | Two Signal types: worker wire `harness.protocol.Signal` (frozen v1, `info`, no evidence) and orchestrator `schemas.signal.Signal` (`note`, `evidence`); convert with `Signal.from_worker` | T1.1 |
| D26 | Names (params, parts, connectors, joints) = lower_snake_case, not a FreeCAD unit symbol (`schemas/names.py`, shared with the SDK). Schemas check structure only; cross-refs/tree/DOF are the plan validator's (signals, not exceptions) | T1.1 |
| D27 | SDK: every length is a `Q` (value + FreeCAD expression); literals auto-promoted to Params aliases (`source: literal`), card dims to `<component>_<param>`, profile fits to `fit_*` / `tap_m3`, so every dimension is GUI-editable (no `fixed:` tag). Literals in sums carry the unit (`+ 3 mm`; unitless fails). Sheet columns name/value/min/max/source. `agenticcad` = alias of `sdk` | T1.2 |
| D28 | Frames (connector LCS, datum frames, mount frames, channels) = one whole-Placement expression `placement(vector(x; y; z); rotationz(yaw) * rotationy(pitch) * rotationx(roll))` (LCS `.Placement`, datum `.AttachmentOffset` on body XY); origin **and** orientation bound to Params (`<name>_yaw/_pitch/_roll`, deg; `rot=` arg alternative to z/x). Channels: yaw/pitch = atan2/acos of end − start. Keep-outs attach to the mount frame datum with a local z offset. Per-component `.Rotation.Yaw` bindings rejected (gimbal-lock fights at pitch 90°). User confirmed D27/D28 2026-10-09: editability first | T1.2 |
| D29 | Card values may be param expressions (`"spline_top + horn_top_above_tip - horn_disc_t"`, + - * / only, `components/card.py`); SDK and generators bind them as FreeCAD expressions. Card frame = the host surface the component rests on, +z out of it (servo flange underside, bay floor top, board recess top). Electronics cards share `volume` / `standoffs` / `openings` / `keep_out` interfaces | T1.4 |
| D30 | `mate_to("<comp>.horn", at)`: card `horn` connector = horn disc underside (same axis as `output_shaft`); driven part's face at z = 0, recess + M2 taps + screwdriver access cut above it. `electronics_bay` floor_t default = `max(3 mm, deepest card cut below the frame + min_wall)` (live FreeCAD `max()`); slotted standoffs = slot + M3 nut trap from the floor underside | T1.4 |
| D31 | L0 is a guardrail, not a sandbox: SDK-only surface (no `_attrs`, no raw `.doc/.body/.sheet/.obj`), worker process = boundary. compile_part = L0 → worker (L1, L2, local L3, save .FCStd) → plan-side L3 on the orchestrator; feedback = status line + ≤ 8 signals, failures first. probe_part reopens the .FCStd read-only; snippet's last expression is the result. Stale .FCStd deleted before each compile | T1.5 |
| D32 | Assembly = `assembly.FCStd` saved first, then one `App::Link` per part/card model .FCStd (relative external links; reopening loads them; a part param edit + `solve()` propagates). Cards without a generator → envelope App::Part (LCS per connector + `interfaces.volume` box) inside the assembly. Component attach = WB Fixed joint `attach_<id>` (card mount connector = the LCS at the card frame origin). Plan→WB types: revolute/continuous/gears → Revolute, slider/rack_pinion → Slider (`Offset1 = Tz(d)` drives it), cylindrical, ball, fixed. Residual gate 1e-4 mm / 1e-4°, gate L5. The WB does not store q: a free slider absorbs a length edit along its axis; poses come from our FK only | T1.6 |
| D33 | L6 static torque = pure-Python gravity term (`validate/statics.py`: moment of downstream weights about each joint axis), exactly RNEA(q, 0, 0) without Pinocchio on Windows; Pinocchio stays in D2 and cross-checks it. Allowed = stall@6V × card derating × servos_on_axis; > 85 % warning. Reach = max horizontal distance tool → root moving joint origin over a dense grid (19/joint, ≤ 4000 poses) | T1.7 |
| D34 | L5 interference: owners placed by our FK (no WB solve), OCCT `common` > 1 mm³ fails; rest + grid 9/joint decimated to ≤ 25 poses; pairs in one rigid group (attach / fixed) checked at rest only; `AssemblyPlan.allow_overlap` [(a, b)] exempts pairs. L7-basic: build volume by sorted extents, watertight via trimesh, min wall by inward ray cast per facet, fail if thin area > 1 mm² | T1.7 |
| D36 | Exporter: fixed plan joints merge into rigid-group links (no URDF fixed joints except tool0); link named after its group root owner; URDF numbers written %.12g; cylindrical/ball not exported in P1 | T1.8 |
| D35 | MG996R travel stays ±90° on the card (user 2026-10-09), although the datasheet says ~120°; verify on a sample (O12) | user |
| D37 | Packs are discovered, never named by the core: `sdk/__init__` calls `packs.load_composites()` (imports every `packs/<id>/composites.py`, which registers methods on `Part`); pack `__init__.py` = metadata only (no FreeCAD), so the orchestrator reads knowledge/bench as data. Reach check stays in core gates | T1.10 |
| D38 | `harness/pipeline.run_job(job_dir)` = the one ordered stage list (plan → parts → assembly L5/L6 → print L7 → build → export → description), stops at the first failing stage, writes `out/report.json`; agents and the bench reuse it. Golden jobs live in `bench/golden/<id>/` | T1.12 |
| D39 | Checkpoint after the plan validates, before any part agent runs (it needs the joint tree + torque estimate; parts are the expensive step). Resolver is deterministic in P1 (requested cards + MG996R default for the manipulator pack + generic bay only for actuated designs, or named boards); LLM resolver when the library grows (P3). Design agent cannot change the Spec (requirements are overwritten on submit) and may only use BOM cards | T1.9 |
| D40 | Router = table (`agents/router.py`): plan → DESIGN; part L0–L3 → CODE part; L5 overlap → CODE for the plan parts in the pair (DESIGN if none); reach/tree/L6 → DESIGN; L7 build volume → DESIGN, other L7 → CODE; build/export/description → STOP (pipeline defect). After a DESIGN revision only parts whose PartSpec / attached components / joints changed are regenerated | T1.9 |
| D41 | Live runs avoid B1-3 for agent evaluation: the golden B1-3 is the worked example in the design and part prompts (leakage) | T1.9 |
| D42 | Agent SDK system prompt always goes through `--system-prompt-file` (temp file per session): inline it broke the Windows 32k command-line limit (WinError 206, reported by the SDK as CLINotFoundError) once pack knowledge + 4 cards were in the design prompt | B1-5, 2026-10-10 |
| D43 | A CODE route whose parts have spent `part_rounds` on an assembly/print failure becomes a design rollback while rollbacks remain (extends D40; the layout, e.g. joint limits vs a wall, is the problem) | B1-1, 2026-10-10 |
| D44 | Pad/Pocket direction via `SideType` (`One side` / `Symmetric`), never `Midplane` (deprecated in FreeCAD 1.1, warns on every reopen) | user GUI check 2026-10-10 |
| D45 | Dress-ups (fillet, chamfer, shell) name a pad/pocket + a cap class (`start`/`end`/`caps`), never edge indices; the SDK picks the edges geometrically from that feature's tool shape and inserts the dress-up right after it (Tip moved and restored), so later cuts cannot split its edges. Edges along a pad are not offered (FreeCAD 1.1 renames them every recompute): round them in the sketch (`rect(r=)`). Transformed features: set `body.Tip` explicitly | S13, S14 2026-10-10 |
| D46 | `bearing_seat`, `horn_coupler`, `link_bar`, `hollow_link`, `turntable_base` live in the manipulator pack (plan §3.3: promoted to core once a second pack needs them). `turntable_base` = servo shaft-up on the base axis + radial-axle 623 rollers whose tops are level with the horn face (turret rides on them). Card ids starting with a digit get the `<family>_<id>` param prefix (`bearing_608zz_outer_d`) | T2.1 2026-10-10 |
| D47 | L4 flex varies declared ranges (plan params, `params(x={value,min,max})`) **and** named literal part params (`params(x=5)`, mm, ≠ 0) over ±20 % (user 2026-10-10); auto-promoted literals, card, profile and expression cells never flex; `min == max` pins a param. Order: connector-driving (transitive through cells) first, declared before implied; cap 30 runs. Seed = part name (not job id), so agent and pipeline compiles flex identically. Failures block compile_part; the saved .FCStd is always nominal | T2.2 |
| D24 | Linux-side checks (check_urdf, `gz sdf -p`, pinocchio; later ROS 2 / Gazebo gates) run in Docker image `agenticcad-ros:jazzy` built from `docker/ros-tools/Dockerfile`; package dir mounted read-only. WSL Humble is a fallback (`AGENTICCAD_LINUX=wsl`) | T0.7 |

## 6. Open items / blockers

| # | Item | Resolved by |
|---|---|---|
| O11 | Live ClaudeAPIBackend run. **Deferred to P5** (user 2026-10-09): prototype and bench run on the Agent SDK + subscription (D4); the API backend stays offline-tested until productization | P5 |
| O9 | Assembly WB `Gears`/`RackPinion` headless | P3 S12 |
| O13 | Opening B1-7 drawer.FCStd in the GUI logs once `Unit mismatch in minus operation` for `Params.box_w - Params.wall * 2` (drawer#inner.Constraints); values are right, recompute is clean, headless open does not reproduce it. Suspect GUI restore order with alias-to-alias cells (`box_w = drawer_w`). Cosmetic; revisit if it shows on other parts | P2 (low) |
| O12 | Datasheet pass 2026-10-09: ESP32 DevKitC (Espressif drawing) and PCA9685 (Adafruit fab print) now `verified: true`; M3 insert hole matches vendor charts. Still unverified: MG996R hole pitch, flange height, shaft offset, horn disc (no vendor drawing exists); travel kept ±90° (D35). Measure a sample when available | user (hardware) |

## 7. File map

| Path | What |
|---|---|
| `implementation_context/IMPLEMENTATION_PLAN.md` | full plan v0.4 (moved here from repo root) |
| `implementation_context/phase_*.md` | per-phase specs, tasks, exit criteria, prev/next notes |
| `REFERECES/REFERENCE_GUIDE.md` | index of papers and research notes |
| `REFERECES/freecad_headless_scripting_research_report.md` | FreeCAD API notes (corrected; tags resolved against 1.1.4 in P0) |
| `docs/env.md`, `docs/spike_results.md`, `docs/worker_protocol.md` | env facts; spike answers; frozen worker protocol v1 (P1 reads these) |
| `harness/` | worker protocol: `run.py` (orchestrator), `worker.py` (freecadcmd), `protocol.py` |
| `spikes/` | `run.py` runner, `sNN_*.py`, `drivers/` (orchestrator-side), `results/` (generated) |
| `components/` | cards (`<family>/<id>.yaml`), `card.py` (load/resolve), `build.py` (CLI), `generators/` |
| `schemas/` | pydantic contracts; `names.py` is FreeCAD-safe; `json/` generated |
| `profiles/` | print profiles + materials (pure-Python loader) |
| `sdk/` | FreeCAD-native SDK v0 (runs in freecadcmd); `docs/sdk.md` = agent-facing reference |
| `assembly/` | `kinematics.py` (tree + FK, pure Python), `owners.py` (worker: load parts/cards/envelopes), `writer.py` (worker op `build_assembly`), `builder.py` (orchestrator) |
| `export/`, `description/` | URDF package writer; D1/D2/D4 checks (Docker) |
| `validate/` | `plan_check.py`, `assembly_check.py`, `printability.py` (orchestrator); `sweep.py` (worker ops); `poses.py`, `statics.py`, `masses.py` (pure Python) |
| `llm/` | `LLMBackend` + `AgentSDKBackend` / `ClaudeAPIBackend`; `tools.py` (`ToolSpec`, `PartWorkspace`) |
| `Articad Architecture diagram.png` | ArtiCAD Fig. 2 (mapped in plan §1.5) |
