from agenticcad import Part


def build(P):
    """Catch block screwed to the base by two M3 screws from the top, beside the window the cover's hook lip
    snaps into (screw heads sit outside the cover and its tab)."""
    part = Part("catch")
    p = part.params(block_w=24, block_t=6, block_h=18, screw_x=9)
    part.pad(part.sketch("block", on="XY").rect(p.block_w, p.block_t, center=(0, -p.block_t / 2)), p.block_h)
    part.connector("mount", origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0), label="catch_mount")
    part.connector("latch", origin=(0, -p.block_t, 20), z=(0, 0, -1), x=(0, 1, 0), label="latch")
    part.snap_hook("latch", "b", 15, 2, 10)
    part.hole("XY", at=[(p.screw_x, -p.block_t / 2), (-p.screw_x, -p.block_t / 2)], fit="clearance_m3", horizontal=True,
              name="screws")                       # the block prints on its side
    return part
