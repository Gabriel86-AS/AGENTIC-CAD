# Manipulator pack — design notes (P1, static; goes straight into the prompts)

## A. Requirement analysis (requirements + resolver agents)

**Infer when the prompt is silent**
| Item | Default | Why |
|---|---|---|
| Servo, main joints | `servo/mg996r` (stall 1.08 N·m @ 6 V, derating 0.5 → design ≤ 0.54 N·m) | D6 |
| Servo, wrist / gripper | MG90S / SG90 (cards arrive in P3) | D6 |
| Electronics | `electronics/generic_bay` (75 × 60 × 25 mm usable) in the base, unless boards are named | D7 |
| Named boards | ESP32 DevKit → `electronics/esp32_devkit`; "servo driver" / PCA9685 → `electronics/pca9685` | cards |
| Material | PETG (PLA only if asked) | D8 |
| Payload | 50 g at `tool0` if the prompt says "arm" without a number; camera mount: 30 g | D9 |
| Joint travel | ±90° (MG996R card range) | D35 |

**Ask (checkpoint) only if** the reach or payload makes L6-plan fail with every servo option, or the prompt
names a part with no card.

**Quick torque estimate (L6-plan does this; use it to choose lengths before planning)**
`τ_shoulder ≈ g · (m_payload · r_tool + Σ m_downstream_servo · r_servo + m_link · r_link / 2)`,
links ≈ 0.5 g/mm. Example B1-3 (150 mm, 50 g, no elbow): 0.074 + 0.055 ≈ 0.13 N·m — well under 0.54.
Each servo carried by the shoulder adds 55 g at its distance: an elbow servo at 100 mm costs 0.054 N·m.

**Typical proportions (2-DOF)**
- Yaw + shoulder: base Ø 90–120 mm, turret height 50–70 mm, upper arm = reach − (shaft offset ≈ 10 mm).
- Shoulder + elbow (planar): upper arm ≈ forearm ≈ reach / 2; keep the elbow servo close to the shoulder
  side of the upper arm, horn toward the forearm.
- Pan-tilt: pan servo vertical in the base, tilt servo on a turret; the payload plate is a `servo_u_bracket`
  on the tilt horn (the bracket's bridge carries the camera mount).

## B. Component and connector analysis (design agent)

**Joint at a servo (D13 mating rule).** Joint parent connector = the holder part's connector where the servo's
card frame (`body_mount`: flange underside, +z toward the shaft) sits; child connector = the driven part's
connector at the horn face (`horn`: disc underside, same axis). Both connectors share the shaft axis; the
child's z points from the servo into the driven part. In the part programs:
- holder: `part.mount("<servo>", at="<conn>")` cuts the case pocket, M4 taps and the cable keep-out;
- driven part: `part.mate_to("<servo>.horn", at="<conn>")` or the composite below.

**`part.servo_u_bracket(servo, at, length=None, width=30, thick=5, gap=2, idler="clearance_m3", horizontal=False)`** —
use it whenever a servo drives a link that carries load (shoulder, elbow, tilt). Plate A sits on the horn
(recess, M2 taps, screwdriver access), plate B passes under the case bottom with an idler bore on the
shaft axis, and a bridge at `x = length` joins them. Default length clears the flange in the swept
half-plane (±90°). Returns `length, width, thick, gap, bridge_x, plate_b_z, mid_z` (Qs in the frame of
`at`): extend the link from the bridge outer face (`x = bridge_x`, centred at `z = mid_z`).
A U-bracket link prints on its side (plates vertical): put the beam flush with one bracket edge
(`center=(0, (beam_t - width) / 2)` on a datum at the bridge) so nothing floats, and pass
`horizontal=True` (the idler bore then lies horizontal). Golden B1-3 `arm.py` does this.
The idler needs a coaxial pin or M3 screw fixed to the holder below the case: give the holder a lug
under the servo with a hole on the shaft axis. Do **not** declare the idler as a joint (no closed loops,
D10); L5 checks the clearance.

**Other composites (all in the frame of their connector; see docs/sdk.md)**
- Yaw joint: `part.turntable_base("<yaw servo>", at=...)` in the base part; the turret is driven through the
  horn (`mate_to` or `horn_coupler`) and its flat underside, at the horn face, rides on the rollers: make the
  turret disc at least `2 × (roller_r + 3)` wide (≈ 100 mm with the defaults).
- Thin links: `part.horn_coupler(servo, at=...)` gives a link narrower than the horn a hub; continue the link
  with `part.link_bar` / `part.hollow_link` (open cable trough) on a datum frame at the joint.
- Bearings: `part.bearing_seat("bearing/608zz", at=...)` (press fit) for an idler or a passive axle; the
  connector is the seat floor, one bearing width below the entry face.

**Rules of thumb**
- Servo holders: the case drops into a pocket from the flange face; leave ≥ 3 mm wall around the pocket
  (`min_wall` of the profile is the floor, not the target).
- Keep every horn-driven part off the servo case by ≥ 2 mm across the travel (U bracket `gap`, plate A
  above the case top); L5 fails above 1 mm³ overlap.
- Yaw joint: turret on the base servo horn with `mate_to`; the turret holds the shoulder servo with its
  shaft horizontal. Prefer `turntable_base` under it (rollers take the tipping load); without it, a large
  turret disc (≥ 2× horn disc) spreads the tipping load onto the horn screws.
- Electronics bay in the base, opening (cable exit) toward the turret; the bay moves to the side of the
  base when the prompt asks, never onto a moving link in P1.
- Exactly one `tool` connector, at the end of the last link (`tool0` in the URDF).
- Every length that a user might change (link lengths, base size) is a plan param with min/max; parts read
  it as `P.<name>`.
