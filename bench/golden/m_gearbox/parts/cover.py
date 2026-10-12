from agenticcad import Part


def build(P):
    """Dust cover (frame = the base frame when closed): top plate + walls ending 0.5 mm above the plate, the
    moving half of the hinge (webs reach 1 mm into the back wall), a front tab carrying the latch hook. The
    hinge webs, tab and hook follow the cover's size, so they stay attached when a dimension changes."""
    part = Part("cover")
    p = part.params(cover_w=90, cover_d=70, cover_cx=25, bottom_z=0.5, top_z=20, top_t=2, wall=2,
                    tab_w=14, tab_d=9, tab_cx=27, hinge_off=5, hinge_z=4.5, catch_gap=0.5, catch_t=6)
    hinge_y = p.cover_d / 2 + p.hinge_off      # the axis stays behind the back wall
    bottom = part.datum("bottom", plane="XY", offset=p.bottom_z)
    part.pad(part.sketch("shell", on=bottom).rect(p.cover_w, p.cover_d, center=(p.cover_cx, 0)),
             p.top_z + p.top_t - p.bottom_z)
    inner = part.datum("inner_top", plane="XY", offset=p.top_z)
    part.pocket(part.sketch("cavity", on=inner).rect(p.cover_w - p.wall * 2, p.cover_d - p.wall * 2,
                                                     center=(p.cover_cx, 0)), depth=p.top_z)
    part.pad(part.sketch("tab", on=inner).rect(p.tab_w, p.tab_d, center=(p.tab_cx, -(p.cover_d + p.tab_d) / 2)), p.top_t)
    part.connector("hinge", origin=(60, hinge_y, p.hinge_z), z=(-1, 0, 0), x=(0, 0, 1), label="hinge")
    part.hinge("hinge", "b", 70, knuckles=5, pin="m3", web=p.hinge_off + 1, web_angle=-90,
               horizontal=True)
    # latch: beam root under the tab, hanging down (z = -Z), lip toward the catch (+y)
    part.connector("latch", origin=(p.tab_cx, -(p.cover_d / 2 + p.catch_gap + p.catch_t), p.top_z),
                   z=(0, 0, -1), x=(0, 1, 0), label="latch")
    part.snap_hook("latch", "a", 15, 2, 10)
    return part
