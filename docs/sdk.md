# AgenticCAD SDK v1 — reference for part programs

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
  symbol (`h`, `m`, `s`, `g`, `t`, `w`, `bar`, `deg` ... are rejected): prefer `height`, `width`, `thick`.
- Plan params are already in the sheet: use `P.name` directly. `part.params(name=P.name)` just
  re-exports it; any other value under a plan param's name is rejected.
- Sketches attach to origin planes or datums, never to faces.
- Gate L4 (flex) moves params and rebuilds: plan params and `part.params(x={"value", "min", "max"})`
  over their range, other named part params (`part.params(wall=3)`) over ±20 %. Derive sizes and
  positions from params, never from numbers that only fit the nominal (a datum on top of a pad uses
  the pad's param: `offset=p.thick`, not `offset=5`). A param that must not move:
  `part.params(x={"value": 5, "min": 5, "max": 5})`. Literals passed straight to calls do not flex.

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
| `sketch.rect(w, h, center=(0,0), r=None)` | axis-aligned rectangle; `r` rounds its corners (= the edges a pad makes along its normal) |
| `sketch.circle(d, center=(0,0))` / `sketch.circles(d, [pts])` | circle / equal circles |
| `sketch.slot(start, end, width)` | obround between two arc centres |
| `sketch.polygon(n, d, center=(0,0))` | regular n-gon in circle d, flat edge at the bottom |
| `sketch.polyline([(u, v), ...])` | closed polygon (≥ 3 points, closes itself); each vertex is its own pair of params |
| `part.pad(sketch, length, reverse=False, symmetric=False)` | extrusion |
| `part.pocket(sketch, depth=… \| through=True, symmetric=False)` | cut |
| `part.hole(on, at, d=None, fit="clearance", depth=None, horizontal=False, counterbore=False, name=None)` | hole(s); diameter from the print profile: `fit` = `clearance`/`press`/`close` (d + allowance), `clearance_m3`/`tap_m3`/... (d ignored) or `insert_m3` (heat-set insert hole from card `fastener/m3_insert`, card depth by default); `horizontal=True` adds the horizontal-hole allowance; through both ways unless `depth`. `counterbore=True` (screw clearance fits) sinks the head (card `fastener/m3_screw`) against the normal of `on`: put `on` at the entry face, normal pointing out |
| `part.connector(name, origin, z, x, label)` or `rot=(yaw, pitch, roll)` | `App::LocalCoordinateSystem`; joints mate connector frames (z = joint axis); origin + orientation bound like `datum`. `rot` angles: numbers or params with unit `deg`; R = Rz(yaw)·Ry(pitch)·Rx(roll) |
| `part.mount(component, at=connector, host_t=None)` | seats a plan component (or card id) with its card frame on the connector: body pocket (+ clearance), tapped mounting holes, standoffs (board cards), keep-out cuts. Card dims become `<component>_<param>` aliases; the cuts move and turn with the connector's params. Slotted standoffs (generic bay) need `host_t` (material under the frame) for their nut traps; prefer `electronics_bay` |
| `part.mate_to("<component>.<card connector>", at=connector, access=True)` | this part is driven by the component there, e.g. `mate_to("s_shoulder.horn", at="shoulder_horn")`: frames coincide, z points from the servo into this part, the part's face rests at z = 0. Cuts the horn disc recess (+ clearance) above z = 0, M2 tap holes for the horn screws, and (access) a screwdriver hole through the part on the axis. Mate the joint on the same axis |
| `part.electronics_bay(component, at=connector, wall=None, margin=2, floor=None)` | open-top bay for an electronics card (`electronics/generic_bay` or a board card such as `electronics/pca9685`, `electronics/esp32_devkit`), card frame on the connector (z up from the floor): floor below z = 0 (default: deep enough for the card's cuts + min wall, follows the card), walls around the card `volume` + margin up to its top, the card's standoffs / recess / keep-outs, and wall openings (cable exit, connector cut, USB plug). Params `<bay>_wall`, `_margin`, `_floor_t` |
| `part.servo_u_bracket(servo, at=connector, length=None, width=30, thick=5, gap=2, idler="clearance_m3", idler_d=None, access=True, horizontal=False)` | *manipulator pack.* Link driven by a servo and supported on both sides: `at` = the servo's `horn` face (as `mate_to`). Plate A on the horn (recess, M2 taps, access), plate B under the case bottom (+ `gap`) with an idler bore on the shaft axis (pin/screw fixed to the holder; not a joint), bridge at x = `length` (default: clears the flange over ±90° + 3 mm). Returns `length, width, thick, gap, bridge_x, plate_b_z, mid_z` in the frame of `at`; continue the link from x = `bridge_x`; `horizontal=True` when it prints on its side (idler bore allowance). Params `ubracket_<servo>_length`, `_width`, `_thick`, `_gap` |
| `part.bearing_seat(bearing, at, through=True, fit="press", horizontal=False)` | *manipulator pack.* press-fit bore for a ball bearing (`bearing/608zz`, `bearing/623zz` or a plan component) whose card frame (seat floor, z through the bearing) is on connector `at`: bore `outer_d` + fit, `width` deep above the floor (put `at` one width below the entry face), and a lip hole (housing-shoulder diameter) on through the part. Returns `outer_d, depth, lip_d, bore_d` |
| `part.horn_coupler(servo, at, wall=3, thick=None, access=True)` | *manipulator pack.* round hub (horn disc + clearance + 2 × wall, default thick = horn recess + screw depth + min wall) padded from the horn face into the part, then `mate_to` cuts: lets a link narrower than the horn take it. Returns `hub_d, thick` |
| `part.link_bar(on, length, width, thick, end_holes=None, hole_d=None, reverse=False)` | *manipulator pack.* round-ended bar from (0, 0) to (length, 0) in the sketch frame of `on` (a datum frame at the joint, `rot=` to aim it), `thick` along the normal; `end_holes` = a hole fit at both end centres |
| `part.hollow_link(on, length, width, thick, wall=2, end_solid=None, ...)` | *manipulator pack.* `link_bar` + an open cable trough in its far face (width − 2 × wall, from x = end_solid to length − end_solid; default end_solid = width), prints without supports |
| `part.turntable_base(servo, at, bearing="bearing/623zz", base_d=None, rollers=3, protrude=1, floor=3, wall=3)` | *manipulator pack.* yaw-joint base: servo shaft-up with its card frame on `at`; cylinder on the shaft axis from `floor` under the case to the ring top (horn face − protrude), servo pocket + taps + keep-out, open well for flange and case top, and `rollers` 623 bearings on radial axles whose tops are level with the horn face (the turret rests on them). Returns `base_d, roller_r, top_z, horn_z, axle_z, bottom_z, axis_x` (frame of `at`); the turret's underside must cover `roller_r ± bearing width / 2` |
| `part.hinge(at, side, length, knuckles=3, od=None, pin="m3", gap=0.5, web=0, web_angle=0, relief=False, horizontal=False)` | *mechanisms pack.* pin hinge, called by **both** parts on connector `at` (same frame; z = hinge axis, knuckles z = 0..length in `knuckles` segments with `gap` between): side `"a"` builds the even knuckles and holds the pin, `"b"` the odd ones and turns on it. `web` > 0: each knuckle is an obround from the axis `web` mm along `web_angle` (deg in the frame's xy plane) into the leaf. pin `"m3"` (a tap, b clearance), `"m3_bolt"` (both clearance: screw + nut, friction pivot), `"filament"` (1.75 mm). `relief=True` cuts room (od + 2 × gap) for the other side's knuckles. Returns `length, od, gap, seg` |
| `part.slide_rail(at, side, length=None, width=8, height=4, flare=2, offset=(0, 0), angle=0, embed=1, fit="clearance")` | *mechanisms pack.* linear guide along z of `at` (= the slider joint axis), both parts on the same frame: profile at `offset` in the frame's xy plane, turned `angle` deg, standing on the host face at local y = 0 and growing to +y (`width` → `width + flare` at `height`: dovetail; flare 0 = flat rib). Side `"a"` = rail, padded z = 0..length; `"b"` = groove + fit (half per side and on top), over z = 0..length or through (`length=None`). Returns `width, height, flare, clearance` |
| `part.spur_gear(at, module, teeth, thick, bore="clearance_m3", bore_d=None, mesh="tooth", mesh_angle=0, pressure_angle=20)` | *mechanisms pack.* involute spur gear on z of `at` (z = 0..thick; FreeCAD involute gear, `<name>_teeth` (unitless, whole ≥ 8) and `<name>_module` (≥ 0.8) are Params). At rest a tooth (`"tooth"`, the driver) or a gap (`"gap"`, the follower) is centred on `mesh_angle` (deg): point it at the mating gear. Centre distance module × (t1 + t2) / 2 (+ ≤ 0.2 backlash); follower joint `gears` with mimic multiplier −t1/t2. `bore` = a hole fit on the axis (None: solid). Returns `module, teeth, thick, pitch_d, tip_d, root_d` |
| `part.snap_hook(at, side, length, thick, width, overhang=1.0, ramp=30, embed=1, fit="clearance")` | *mechanisms pack.* cantilever snap fit, both parts on connector `at` (same frame, latch closed): z from the beam root (host face) to its tip, lip toward +x. Side `"a"`: beam x = −thick..0 (y ± width/2) with a lip `overhang` deep, entry ramp `ramp` deg off z, flat retaining face toward the root; `"b"`: window for the lip (+ fit) in the mating wall, which fills x ≥ 0. Fails if the bending strain 1.5·thick·overhang / (length − lip)² exceeds 3 % (PETG) / 1.5 % (PLA). Returns `length, thick, width, overhang, lip_h, strain` |
| `part.cable_channel(start, end, d)` | straight round channel between two part-frame points; editing either end moves and turns it |
| `part.fillet(feature, r, edges="end")` / `part.chamfer(feature, size, edges="end")` | round / bevel the cap edges a pad or pocket made: `"end"` (pad top, pocket floor), `"start"` (pad base, pocket rim: countersink) or `"caps"`. `feature` = what `pad`/`pocket` returned, or its name. Inserted right after that feature, so later cuts never break it. Vertical (along-the-pad) edges: use `rect(..., r=)` in the sketch |
| `part.shell(feature, wall, open="end")` | hollow the solid inward to `wall`, removing that feature's cap face(s) (`"end"`, `"start"`, `"caps"`) |
| `part.mirror(features, plane="YZ")` | mirror copies of a feature or list, about `"XY"`/`"XZ"`/`"YZ"` or a datum |
| `part.pattern_linear(features, direction, spacing, count)` | `count` copies (original included) along `"x"`/`"y"`/`"z"`/`"-x"`… or a datum's normal; `<name>_count` is an editable unitless param |
| `part.pattern_polar(features, axis="z", count=2, angle=360)` | `count` copies about an origin axis; 360 = evenly round, else spread over `angle` with both ends included |
| `part.boss(on, at, d, height, hole=None, hole_d=None, hole_depth=None)` | cylinder boss(es) at `at` (point or list) rising along the normal of `on`; `hole` = a fit (`"insert_m3"`, `"tap_m3"`, `"clearance_m3"`, `"press"` + `hole_d`) cut from the boss top, boss-height deep (insert: card depth) |
| `part.rib(on, points, thick)` | gusset/rib: `polyline(points)` on `on`, padded `thick` symmetric about it |
| `part.split_joint(at, side, height, by="dovetail", length=12, width=14, flare=4, screw="m3", screws=2)` | joint feature on one half of a part split for the build volume. Both halves declare connector `at` on the cut face with the **same** frame (z from half `a` into half `b`, x along the face); the plan joins them with a `fixed` joint. `dovetail`: a gets a tongue `width` → `width + flare` over `length` along z, `height` thick along the frame's y (slides in along y); b gets the slot + close fit, through b along y. `screws`: rectangular tongue `width` × `length` × `height` with `screws` tap holes across it; b gets the pocket + close fit and clearance holes along y (b needs ≥ min wall around the pocket). Returns `length, width, height, clearance` |

Not in the SDK: spline (hornless) servo interfaces; sweeps/lofts; fillets on edges picked by index (they break on edits).

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
| `bearing/608zz` (8 × 22 × 7), `bearing/623zz` (3 × 10 × 4) | `bearing_seat`; 623 = `turntable_base` rollers (M3 screw axles). Params as `bearing_608zz_<param>` when named by card id | `seat` (face on the shoulder), `axis` |

Cards with `verified: false` hold nominal values; the checkpoint shows them for measurement.

## Print gate (L7, after the assembly gates)
Each part is meshed and placed in its best print orientation (6 axis-aligned + largest flat face down;
the least support area, load paths between connectors kept out of the layer direction). Failures:
not a closed manifold solid; walls < 1.2 mm; support area > 15 % of the footprint (a floating face, a
ceiling or span > 10 mm between walls, a cantilever: bring faces down to one flat side, keep spans short,
chamfer undersides to ≤ 45° from vertical); a hole at a bare screw / bearing nominal (use `hole(fit=...)`
or `bearing_seat`); no orientation fits the build volume (design: split the part, `split_joint`). Sliding
or press fit holes lying horizontal in the chosen orientation warn without `horizontal=True`.

## Results
`part.report()` recomputes and returns gates `L1_no_invalid`, `L2_sketches` (solve 0, DoF 0, every
dimension bound), `L3_one_solid`, plus sketches, connectors, params, volume, bounding box and
`signals` (`failure` / `warning` / `info`, worker format). `part.save(path)` writes the `.FCStd`.

## Tools: compile_part and probe_part
`compile_part()` runs L0, builds the part in FreeCAD (L1), checks every sketch (L2) and the solid
(L3), flexes the params (L4: min, max and one interior value each, up to 30 rebuilds; connector-driving
params first; a failure names the param, the value and the first broken feature), then checks the
plan: each plan connector exists within 0.01 mm of its declared pose with its label, and the bounding
box is within ±20 % of the plan's `bbox_mm`. The saved model is always the nominal one. It answers with one status
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
