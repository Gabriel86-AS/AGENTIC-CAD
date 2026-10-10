# Core mechanisms (no pack): passive hinges and slides

Notes for designs without a domain pack (boxes, lids, drawers, clamps ...). Everything is printed; there is
no actuator unless the user names one.

## Passive joints
- `driver: null`; limits need numbers: revolute `{lower, upper}` in rad, `effort` N·m, `velocity` rad/s;
  slider `{lower, upper}` in mm, `effort` N, `velocity` mm/s (e.g. effort 5, velocity 1 / 100).
- No `tool` connector unless the request has a reach or payload; L6 torque is not checked without drivers.
- `dof` counts every independent moving joint, passive ones included.
- q = 0 is the closed / pushed-in state; choose the joint axis direction so that positive q opens.
  Right-hand rule about the parent connector's z: a lid that extends toward −y from a hinge axis along x
  opens upward with z = [-1, 0, 0] and limits [0, opening angle].
  A drawer pulled out toward −y: slider z = [0, -1, 0], limits [0, travel].

## Clearance (L5 fails any overlap > 1 mm³ at rest and across the joint range)
- Hinge: alternate knuckles on the two parts along the axis, ≥ 0.5 mm axial gap between neighbours; the
  pin is an M3 screw (not modelled): `hole(fit="clearance_m3", horizontal=True)` through the moving part's
  knuckles, `fit="tap_m3"` in the fixed part's. Put the axis outside the box wall and above its rim
  (≥ knuckle radius + 0.5 mm) so the lid edge clears the wall while it swings; check the arc at the
  upper limit.
- Slide: 0.5 mm gap per side between the drawer and the frame's inner walls, floor and top; rails or a
  plain box guide both work. A front stop (lip) is optional; the travel limit lives in the joint.
- Parts in the same rigid group are checked at rest only; parts across a joint are checked at every pose.

## Print
- Every part fits the 180 × 180 × 180 mm build volume; walls ≥ 1.2 mm (profile `min_wall_mm`), 2 mm is a
  safe default for box walls and floors.
- Give each part's `bbox_mm` in the plan; the part gate holds the built part to it within ±20 %.
