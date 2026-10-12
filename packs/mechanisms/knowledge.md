# Mechanisms pack — design notes (P2, static; goes straight into the prompts)

Passive printed mechanisms: hinged lids and stands, drawers and slides, gear pairs, snap latches, friction
pivots, pendulums. Everything is printed; there is no actuator unless the user names one (then the
manipulator pack's servo notes apply to that joint).

## A. Requirement analysis (requirements agent)

| Item | Default when the prompt is silent |
|---|---|
| pack | `mechanisms` for any of the above; `null` only for a static object with no moving joint |
| dof | every independent moving joint, passive ones included; a gear follower (mimic) does not count |
| components_requested / electronics | `[]` / `[]` (no actuator, no bay) |
| reach_mm / payload_g | null, unless the user gives a reach (lamp arm) or a load (shelf, tablet ≈ 500 g) |
| Material | PETG (tougher hinges and snaps); PLA if asked |
| Hinge pin | M3 screw (`pin="m3"`); 1.75 mm filament for small boxes if asked |
| Box walls / floors | 2 mm; lids 2 mm; everything inside the 180 mm build volume |

Typical sizes: trinket box 60–100 mm; drawer unit 100–150 mm wide, travel 60–80 % of its depth; linear
slide rail 100–170 mm; gear module 1.5 (≥ 1 for FDM), ≥ 12 teeth; snap beam 12–20 mm long, 1.5–2.5 mm thick;
desk lamp arm links 120–160 mm each; tablet stand 120–150 mm wide, back tilted 60–70° from the base.

## B. Component and connector analysis (design agent)

**Passive joints (core rules hold).** `driver: null`; limits are numbers: revolute {lower, upper} rad with
effort (N·m) and velocity (rad/s), slider {lower, upper} mm with effort (N) and velocity (mm/s), e.g. effort
5, velocity 1 / 100. q = 0 is the closed / folded / pushed-in state; positive q opens (right-hand rule
about the parent connector's z). No `tool` connector unless the request has a reach or payload.

**Two-sided composites.** `hinge`, `slide_rail` and `snap_hook` are called by BOTH parts of a pair on a
connector with the same frame (a joint's two connectors coincide at q = 0 anyway; for a latch, give both
parts a `latch` connector at the same pose with the latch closed). One part passes `side="a"`, the other
`side="b"`. Name the call in the part intent, with every argument, so the two agents build matching halves.

**`part.hinge(at, side, length, knuckles=3, od=None, pin="m3", gap=0.5, web=0, web_angle=0, relief=False, horizontal=False)`**
- Connector `at` on the hinge axis at one end of the knuckle stack; z = the axis (= the revolute joint's
  z), knuckles from z = 0 to `length`. Side "a" (usually the fixed part: base, body, frame) gets the even
  knuckles and holds the pin; "b" gets the odd ones and turns on it. Odd `knuckles` (3, 5) keep "a" at both ends.
- `web` (mm) and `web_angle` (deg in the connector's xy plane) join each knuckle to the part's leaf: aim
  the web from the axis into the leaf, and keep the axis outside both leaves by ≥ od / 2 + gap so the other
  side's knuckles never cut the leaf. `relief=True` cuts that room instead, for a leaf that reaches the axis.
- Pins: `"m3"` (M3 screw: a tapped, b clearance), `"m3_bolt"` (both clearance, screw + nyloc nut: a
  friction pivot that holds a pose — lamp arms, stand legs), `"filament"` (1.75 mm, small boxes).
- `horizontal=True` when the axis lies horizontal in print (adds the horizontal-hole allowance).
- Box with a lid: hinge axis along the back edge, above the rim and behind the back wall
  (≥ od / 2 + 0.5 mm from both); base = "a" with web toward the wall, lid = "b" with web toward the lid.
  Upper limit 100–120°; check that the lid's back edge clears the base at the upper limit (L5 sweeps it).

**`part.slide_rail(at, side, length=None, width=8, height=4, flare=2, offset=(0, 0), angle=0, embed=1, fit="clearance")`**
- Connector `at`: z = the slide direction (= the slider joint's z), both parts on the same frame at q = 0.
  The profile sits at `offset` in the connector's xy plane, turned by `angle`; it stands on the host face at
  local y = 0 and grows toward +y (dovetail: `width` at the face, `width + flare` at the top; flare 0 =
  a flat rib). Side "a" = the rail (on the moving part: drawer, carriage), padded z = 0..length; side "b" =
  the groove + `fit` in the fixed part, `length=None` = through (open at the entry end).
- Drawer: two rails on the drawer's outer side walls (offset to each wall, `angle` ±90 so the rails point
  out of the walls), grooves in the frame's inner walls; 0.5 mm gap between drawer and frame elsewhere;
  front pull lip on the drawer. Travel = slider upper limit (60–80 % of the depth); the frame's back wall is
  the stop at q = 0.
- Linear slide / carriage: one wide dovetail (width 12–16, height 5) on the carriage's underside.

**`part.spur_gear(at, module, teeth, thick, bore="clearance_m3", bore_d=None, mesh="tooth", mesh_angle=0)`**
- Connector `at` on the gear axis at the gear's bottom face; z = the axis = the joint's z. Teeth and module
  are Params (FreeCAD involute gear). Pitch d = module × teeth; tip d = module × (teeth + 2).
- A pair: parallel axes, centre distance = module × (t1 + t2) / 2 (+ 0.1–0.2 mm backlash for print). Point
  `mesh_angle` (deg, in the gear's connector frame) at the other gear's axis: the driver uses
  `mesh="tooth"`, the follower `mesh="gap"`, so the teeth interleave at rest.
- Joints: driver `revolute` (or `continuous`), follower `type: "gears"` with
  `mimic: {joint: <driver joint>, multiplier: -t1 / t2}` (axes parallel, same z direction); the follower
  is not counted in dof. Axles: M3 screws through the gears into tapped bosses (`part.boss(..., hole="tap_m3")`)
  on the base plate; leave 0.5 mm between gear faces and the plate (a washer boss under each gear).
- A crank or knob on the driver: `part.boss` on the gear's top face, off-axis.

**`part.snap_hook(at, side, length, thick, width, overhang=1.0, ramp=30, embed=1, fit="clearance")`**
- Connector `at`: origin at the beam root on the hook part's face, z along the beam toward its tip, x toward
  the catch (the lip protrudes +x). Side "a" (hook) builds the beam x = -thick..0 with the lip at the tip;
  side "b" (catch) cuts a window in its wall, which must fill x ≥ 0 at the lip (the beam rests against
  the wall face at x = 0). The window's root-side edge holds the lip.
- Strain: 1.5 × thick × overhang / (length − lip)² must stay under 3 % (PETG) / 1.5 % (PLA); the call fails
  otherwise. PETG: length 15, thick 2, overhang 1 → 1.7 %.
- Box latch: hook on the lid's front edge hanging down outside the base's front wall (lid = "a"), window in
  that wall (base = "b"). The hook beam prints best along the lid's print plane (lid printed top down).

**Pivots and pendulums.** A pivot is a `hinge` with 3 knuckles (clevis "a" + tongue "b") or 2 (lap joint);
friction pivots use `pin="m3_bolt"`. Metronome: body ("a", ground) with a pivot near its base, the pendulum
("b") swings ±30°; a sliding weight on the pendulum is a `slide_rail` pair (rail on the pendulum, groove in
the weight), slider joint along the pendulum.

**Rules of thumb**
- Every moving pair keeps ≥ 0.5 mm clearance over its whole range (L5 sweeps it; add `clearance` tests for
  leaves that swing past each other).
- Parts print flat: lids top down, boxes on their floor, gears flat, rails along the bed.
- Every size a user might edit (box size, travel, link length, teeth) is a plan param with min/max; teeth
  params use unit "-" and min = max (they are counts).
