"""Manipulator pack v0 (P1 T1.10): servo_u_bracket composite, knowledge.md, bench prompts B1-1..5.
The reach check stays in the core gates (validate/plan_check.py, validate/assembly_check.py): it only
fails when the spec sets reach_mm, so it is inert for other domains."""

PACK = {
    "id": "manipulator",
    "summary": "servo-driven serial arms and pan-tilt heads (MG996R / MG90S / SG90 class servos)",
    "composites": ["servo_u_bracket", "bearing_seat", "horn_coupler", "link_bar", "hollow_link", "turntable_base"],
    # worked example in the design / part prompts (bench/golden/<golden>); its bench case is not used to judge agents
    "example": {"golden": "b1_3", "title": "golden B1-3 (yaw + shoulder arm, 150 mm reach, 50 g)",
                "parts": ["turret.py", "arm.py"]},
}
