"""Mechanisms pack v0 (P2 T2.8): passive and gear-coupled mechanisms (hinged lids and legs, drawers and
slides, gear pairs, snap latches, friction pivots). Composites hinge / slide_rail / spur_gear / snap_hook,
knowledge.md, bench prompts M2-1..8. No actuator unless the user names one."""

PACK = {
    "id": "mechanisms",
    "summary": "passive printed mechanisms: hinged lids and stands, drawers and linear slides, gear pairs, "
               "snap latches, friction pivots (lamp arms), metronome-like pendulums",
    "composites": ["hinge", "slide_rail", "spur_gear", "snap_hook"],
    # worked example (bench/golden/m_gearbox): gear pair + mimic, hinged cover, snap latch on a fixed catch part
    "example": {"golden": "m_gearbox", "title": "golden m_gearbox (12/30-tooth gear pair with a crank on a base "
                "plate, hinged dust cover with a snap latch)", "parts": ["cover.py", "gear_a.py", "catch.py"]},
}
