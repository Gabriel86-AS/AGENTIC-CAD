# AgenticCAD SDK v0 — reference for part programs

A part program is `parts/<name>.py`. It may import only `agenticcad` and `math`, and defines
`build(P)` returning a `Part`. `P` holds the plan params (`P.upper_arm_len`).

```python
from agenticcad import Part

def build(P):
    part = Part("upper_arm", material="PETG")
    p = part.params(length=P.upper_arm_len, width=20, thick=6)
    s = part.sketch("profile", on="XY").slot((0, 0), (p.length, 0), p.width)
    part.pad(s, p.thick)
    part.hole("XY", at=[(0, 0), (p.length, 0)], d=5, fit="press", name="pins")
    part.connector("tip", origin=(p.length, 0, p.thick), z=(0, 0, 1), x=(1, 0, 0), label="tool")
    return part
```

## Rules the SDK enforces
- Gate L0 (before anything runs): imports only `agenticcad` and `math`; no `open`/`exec`/`eval`/
  `getattr`/`__import__`, no FreeCAD modules, no `_private` attributes or raw FreeCAD handles
  (`.doc`, `.body`, `.sheet`, `.obj`); a top-level `def build(P)`.
- Every length becomes an expression of a `Params` spreadsheet alias. Literals are promoted to
  aliases named after the call (`<sketch>_<shape>_<dim>`), so the `.FCStd` stays editable.
- Arithmetic on params: `p.length / 2`, `p.width + 3`, `-p.x` (multiply/divide by plain numbers only).
- Every sketch is fully constrained after each call; otherwise `SDKError` (gate L2) at build time.
- Names (part, params, sketches, datums, connectors) are `lower_snake_case` and not a unit
  symbol (`h`, `m`, `s`, `g`, `deg` ... are rejected).
- Sketches attach to origin planes or datums, never to faces.

## Frames
Part frame = Body origin. Sketch coordinates on origin planes:

| `on` | sketch (u, v) → part | normal (pad direction) |
|---|---|---|
| `"XY"` | (X, Y) | +Z |
| `"XZ"` | (X, Z) | −Y |
| `"YZ"` | (Y, Z) | +X |
| datum | (datum x, datum y) | datum z |

`pad` grows along the normal (`reverse=True` against it, `symmetric=True` both ways); `pocket` cuts against it.

## Calls

| Call | Does |
|---|---|
| `Part(name, material="PETG"\|"PLA")` | new document: `Params` sheet, `App::Part` `<name>`, `Body` |
| `part.params(name=value, ...)` | value: number (mm), a param/expression, or `{"value", "min", "max", "unit"}`; returns `p.<name>` |
| `part.datum(name, plane="XY", offset=0)` | datum plane offset along the plane normal |
| `part.datum(name, origin=(x,y,z), z=(..), x=(..))` or `rot=(yaw, pitch, roll)` | datum frame; origin and orientation bound to params (`<name>_o_x`..., `<name>_yaw`/`_pitch`/`_roll` in deg; zero components stay literal) |
| `part.sketch(name, on="XY"\|datum)` | sketch; returns a `Sketch` |
| `sketch.rect(w, h, center=(0,0))` | axis-aligned rectangle |
| `sketch.circle(d, center=(0,0))` / `sketch.circles(d, [pts])` | circle / equal circles |
| `sketch.slot(start, end, width)` | obround between two arc centres |
| `sketch.polygon(n, d, center=(0,0))` | regular n-gon in circle d, flat edge at the bottom |
| `part.pad(sketch, length, reverse=False, symmetric=False)` | extrusion |
| `part.pocket(sketch, depth=… \| through=True, symmetric=False)` | cut |
| `part.hole(on, at, d=None, fit="clearance", depth=None, horizontal=False, counterbore=False, name=None)` | hole(s); diameter from the print profile: `fit` = `clearance`/`press`/`close` (d + allowance), `clearance_m3`/`tap_m3`/... (d ignored) or `insert_m3` (heat-set insert hole from card `fastener/m3_insert`, card depth by default); `horizontal=True` adds the horizontal-hole allowance; through both ways unless `depth`. `counterbore=True` (screw clearance fits) sinks the head (card `fastener/m3_screw`) against the normal of `on`: put `on` at the entry face, normal pointing out |
| `part.connector(name, origin, z, x, label)` or `rot=(yaw, pitch, roll)` | `App::LocalCoordinateSystem`; joints mate connector frames (z = joint axis); origin + orientation bound like `datum`. `rot` angles: numbers or params with unit `deg`; R = Rz(yaw)·Ry(pitch)·Rx(roll) |
| `part.mount(component, at=connector, host_t=None)` | seats a plan component (or card id) with its card frame on the connector: body pocket (+ clearance), tapped mounting holes, standoffs (board cards), keep-out cuts. Card dims become `<component>_<param>` aliases; the cuts move and turn with the connector's params. Slotted standoffs (generic bay) need `host_t` (material under the frame) for their nut traps; prefer `electronics_bay` |
| `part.mate_to("<component>.<card connector>", at=connector, access=True)` | this part is driven by the component there, e.g. `mate_to("s_shoulder.horn", at="shoulder_horn")`: frames coincide, z points from the servo into this part, the part's face rests at z = 0. Cuts the horn disc recess (+ clearance) above z = 0, M2 tap holes for the horn screws, and (access) a screwdriver hole through the part on the axis. Mate the joint on the same axis |
| `part.electronics_bay(component, at=connector, wall=None, margin=2, floor=None)` | open-top bay for an electronics card (`electronics/generic_bay` or a board card such as `electronics/pca9685`, `electronics/esp32_devkit`), card frame on the connector (z up from the floor): floor below z = 0 (default: deep enough for the card's cuts + min wall, follows the card), walls around the card `volume` + margin up to its top, the card's standoffs / recess / keep-outs, and wall openings (cable exit, connector cut, USB plug). Params `<bay>_wall`, `_margin`, `_floor_t` |
| `part.servo_u_bracket(servo, at=connector, length=None, width=30, thick=5, gap=2, idler="clearance_m3", idler_d=None, access=True)` | *manipulator pack.* Link driven by a servo and supported on both sides: `at` = the servo's `horn` face (as `mate_to`). Plate A on the horn (recess, M2 taps, access), plate B under the case bottom (+ `gap`) with an idler bore on the shaft axis (pin/screw fixed to the holder; not a joint), bridge at x = `length` (default: clears the flange over ±90° + 3 mm). Returns `length, width, thick, gap, bridge_x, plate_b_z, mid_z` in the frame of `at`; continue the link from x = `bridge_x`. Params `ubracket_<servo>_length`, `_width`, `_thick`, `_gap` |
| `part.cable_channel(start, end, d)` | straight round channel between two part-frame points; editing either end moves and turns it |

Not in v0 yet: pack composites such as `servo_u_bracket` (T1.10); spline (hornless) interfaces.

## Components
Cards live in `components/<family>/<id>.yaml` (`read_file` them; never copy their numbers into a
part program). Card frame = the host surface the component rests on, +z out of it. In the library:

| Card | Use with | Card connectors |
|---|---|---|
| `servo/mg996r` | `mount` (case pocket, M4 tap holes, cable keep-out); `mate_to("<id>.horn")` on the driven part | `body_mount`, `output_shaft` (spline tip), `horn` (disc underside, same axis) |
| `electronics/generic_bay` | `electronics_bay` (75 × 60 × 25 usable, slotted M3 standoffs + nut traps) | `bay_mount` |
| `electronics/pca9685` | `electronics_bay` or `mount` (M2.5 standoffs) | `board_mount` |
| `electronics/esp32_devkit` | `electronics_bay` or `mount` (flush recess + pin pits, USB plug cut toward −x; no holes) | `board_mount` |
| `fastener/m3_screw` | `hole(fit="clearance_m3", counterbore=True)`; bay nut traps | `head` |
| `fastener/m3_insert` | `hole(fit="insert_m3")` | `top` |

Cards with `verified: false` hold nominal values; the checkpoint shows them for measurement.

## Results
`part.report()` recomputes and returns gates `L1_no_invalid`, `L2_sketches` (solve 0, DoF 0, every
dimension bound), `L3_one_solid`, plus sketches, connectors, params, volume, bounding box and
`signals` (`failure` / `warning` / `info`, worker format). `part.save(path)` writes the `.FCStd`.

## Tools: compile_part and probe_part
`compile_part()` runs L0, builds the part in FreeCAD (L1), checks every sketch (L2) and the solid
(L3), then checks the plan: each plan connector exists within 0.01 mm of its declared pose with its
label, and the bounding box is within ±20 % of the plan's `bbox_mm`. It answers with one status
line and up to 8 signals, failures first:

```
compile_part: FAIL (1 failure) - volume 11357.5 mm3, bbox 100.0 x 20.0 x 6.0 mm, 1 connectors, ...
- FAILURE L3: connector 'tip' is at o=[80.0, 0.0, 6.0] ...; the plan declares o=[80.0, 0.0, 0.0] ... | at connector tip | fix: ...
```

plus `volume_mm3`, `bbox`, `connectors`, `params` (alias names) and sketch DoF. Locations name the
program line (`parts/link.py:7`) when the error is raised there.

`probe_part(snippet)` measures the last compiled model, read-only. The snippet may use `probe` and
`math`; the value of its last expression is returned:

| `probe.` | Returns |
|---|---|
| `bbox()` | `{min, max, size}` |
| `volume()`, `area()`, `com()` | mm³, mm², centre of mass |
| `connectors()`, `connector(name)` | `{o, z, x, label}` |
| `params()`, `param(name)` | Params values (mm / deg) and sources |
| `sketches()`, `features()` | DoF per sketch; feature types and states |
| `inside(p)`, `distance(p)` | point in material?; distance to the surface |
| `segments(p0, p1)` | parts of the segment inside material, `[[t0, t1], ...]` mm from p0 |
| `thickness(p, direction)` | material thickness through p along ±direction (wall checks) |
