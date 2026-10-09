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
| `part.datum(name, origin=(x,y,z), z=(..), x=(..))` | datum frame (origin bound to params, orientation fixed) |
| `part.sketch(name, on="XY"\|datum)` | sketch; returns a `Sketch` |
| `sketch.rect(w, h, center=(0,0))` | axis-aligned rectangle |
| `sketch.circle(d, center=(0,0))` / `sketch.circles(d, [pts])` | circle / equal circles |
| `sketch.slot(start, end, width)` | obround between two arc centres |
| `sketch.polygon(n, d, center=(0,0))` | regular n-gon in circle d, flat edge at the bottom |
| `part.pad(sketch, length, reverse=False, symmetric=False)` | extrusion |
| `part.pocket(sketch, depth=… \| through=True, symmetric=False)` | cut |
| `part.hole(on, at, d=None, fit="clearance", depth=None, horizontal=False, name=None)` | hole(s); diameter from the print profile: `fit` = `clearance`/`press`/`close` (d + allowance) or `clearance_m3`/`tap_m3`/... (d ignored); `horizontal=True` adds the horizontal-hole allowance; through both ways unless `depth` |
| `part.connector(name, origin, z, x, label)` | `App::LocalCoordinateSystem`; joints mate connector frames (z = joint axis) |
| `part.mount(component, at=connector)` | seats a plan component (or card id) with its card frame on the connector: body pocket (+ clearance), tapped mounting holes, keep-out cuts. Card dims become `<component>_<param>` aliases |
| `part.cable_channel(start, end, d)` | straight round channel between two part-frame points |

Not in v0 yet: `mate_to` (horn interface, needs card horn data, T1.4), `electronics_bay` (T1.4),
pack composites such as `servo_u_bracket` (T1.10).

## Results
`part.report()` recomputes and returns gates `L1_no_invalid`, `L2_sketches` (solve 0, DoF 0, every
dimension bound), `L3_one_solid`, plus sketches, connectors, params, volume, bounding box and
`signals` (`failure` / `warning` / `info`, worker format). `part.save(path)` writes the `.FCStd`.
