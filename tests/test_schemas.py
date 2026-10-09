import copy

import pytest
from pydantic import ValidationError

from harness.protocol import Signal as WorkerSignal
from schemas.export import OUT, render
from schemas.names import name_error
from schemas.plan import AssemblyPlan, Connector, Joint, Param
from schemas.signal import Signal

# plan §3.2 example, plus param ranges (required from P1)
PLAN = {
    "ground": "base",
    "requirements": {"dof": 2, "reach_mm": 220, "payload_g": 50},
    "params": {"upper_arm_len": {"value": 110, "min": 80, "max": 140}},
    "components": [
        {"id": "s_yaw", "card": "servo/mg996r", "attach_to": "base.yaw_servo_mount"},
        {"id": "s_shoulder", "card": "servo/mg996r", "attach_to": "turret.shoulder_servo_mount"},
    ],
    "parts": [
        {"name": "base", "intent": "round base", "connectors": [
            {"name": "yaw_servo_mount", "o": [0, 0, 8], "z": [0, 0, 1], "x": [1, 0, 0], "label": "servo_mount"}]},
        {"name": "turret", "intent": "U-bracket", "connectors": [
            {"name": "yaw_horn", "o": [0, 0, 0], "z": [0, 0, 1], "x": [1, 0, 0], "label": "servo_horn"},
            {"name": "shoulder_servo_mount", "o": [0, 0, 45], "z": [0, 1, 0], "x": [1, 0, 0], "label": "servo_mount"}]},
        {"name": "upper_arm", "intent": "link", "connectors": [
            {"name": "shoulder_horn", "o": [0, 0, 0], "z": [0, 1, 0], "x": [1, 0, 0], "label": "servo_horn"}]},
    ],
    "joints": [
        {"name": "j_yaw", "type": "revolute", "parent": "s_yaw.output_shaft", "child": "turret.yaw_horn",
         "driver": "s_yaw", "limits": {"lower": -1.57, "upper": 1.57}},
        {"name": "j_shoulder", "type": "revolute", "parent": "s_shoulder.output_shaft", "child": "upper_arm.shoulder_horn",
         "driver": "s_shoulder", "limits": {"lower": -0.2, "upper": 2.6}},
    ],
    "dof": 2,
}


def test_plan_example_validates():
    plan = AssemblyPlan.model_validate(PLAN)
    assert plan.part("turret").connectors[1].z == (0, 1, 0)
    assert plan.joints[0].limits.effort == "from_card"


@pytest.mark.parametrize("name,ok", [("h", False), ("deg", False), ("Width", False), ("2x", False),
                                     ("height", True), ("upper_arm_len", True), ("x1", True)])
def test_param_names(name, ok):
    assert (name_error(name) is None) == ok


def test_bad_param_name_and_range():
    bad = copy.deepcopy(PLAN)
    bad["params"]["h"] = {"value": 1, "min": 0, "max": 2}
    with pytest.raises(ValidationError, match="unit symbol"):
        AssemblyPlan.model_validate(bad)
    with pytest.raises(ValidationError, match="min <= value <= max"):
        Param(value=5, min=6, max=9)


def test_connector_frame_checks():
    with pytest.raises(ValidationError, match="must be 1"):
        Connector(name="c1", o=(0, 0, 0), z=(0, 0, 2), x=(1, 0, 0), label="l")
    with pytest.raises(ValidationError, match="must be 0"):
        Connector(name="c1", o=(0, 0, 0), z=(0, 0, 1), x=(0, 0.6, 0.8), label="l")


def test_joint_rules():
    with pytest.raises(ValidationError, match="needs a driver"):
        Joint(name="j_lid", type="revolute", parent="box.hinge", child="lid.hinge", limits={"lower": 0, "upper": 1.5})
    Joint(name="j_lid", type="revolute", parent="box.hinge", child="lid.hinge",
          limits={"lower": 0, "upper": 1.5, "effort": 1.0, "velocity": 1.0})
    with pytest.raises(ValidationError, match="owner"):
        Joint(name="j_x", type="fixed", parent="box", child="lid.hinge")
    with pytest.raises(ValidationError, match="needs limits"):
        Joint(name="j_x", type="slider", parent="frame.rail", child="drawer.rail")


def test_duplicate_owner_names():
    bad = copy.deepcopy(PLAN)
    bad["components"][0]["id"] = "base"
    with pytest.raises(ValidationError, match="unique"):
        AssemblyPlan.model_validate(bad)


def test_unknown_field_rejected():
    bad = copy.deepcopy(PLAN)
    bad["parts"][0]["colour"] = "red"
    with pytest.raises(ValidationError):
        AssemblyPlan.model_validate(bad)


def test_worker_signal_conversion():
    s = Signal.from_worker(WorkerSignal(level="info", gate="L2", cause="ok"))
    assert s.level == "note" and s.evidence == {}


def test_json_schemas_current():
    """Run `uv run python -m schemas.export` after changing a schema."""
    for name, text in render().items():
        assert (OUT / name).read_text(encoding="utf-8") == text, f"{name} is stale"


def test_generic_profile():
    from profiles import hole_diameter, load_materials, load_profile
    from schemas.profile import PrintProfile

    prof = load_profile()
    PrintProfile.model_validate(prof)
    assert prof["wall_count"] * prof["line_width_mm"] == pytest.approx(1.35)
    assert hole_diameter(prof, 8.0, "press") == pytest.approx(8.2)
    assert hole_diameter(prof, 3.0, "clearance_m3", horizontal=True) == pytest.approx(3.6)
    assert set(load_materials()) == {"PETG", "PLA"}
