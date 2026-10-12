"""Spec and AssemblyPlan: the cross-stage contracts (plan §3.2, P1 §4.1).

Validation here is structural only (shapes, units, names, ranges). Cross-references (owners,
connectors, tree, DOF, reach, torque) belong to the plan validator, which returns signals the
design agent can act on instead of raising.
"""
import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from schemas.names import name_error, part_name_error, split_ref

Vec3 = tuple[float, float, float]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_name(v: str) -> str:
    err = name_error(v)
    if err:
        raise ValueError(err)
    return v


def _check_part_name(v: str) -> str:
    err = part_name_error(v)
    if err:
        raise ValueError(err)
    return v


class Spec(_Model):
    """Requirements agent output (spec.json)."""
    pack: str | None = None
    dof: int = Field(ge=0)
    reach_mm: float | None = Field(default=None, gt=0)
    payload_g: float | None = Field(default=None, ge=0)
    components_requested: list[str] = Field(default_factory=list)  # card ids, e.g. "servo/mg996r"
    electronics: Literal["generic"] | list[str] = "generic"
    print_profile: str = "generic_fdm"
    material: Literal["PETG", "PLA"] = "PETG"
    notes: str = ""


class Param(_Model):
    value: float
    min: float
    max: float
    unit: Literal["mm", "deg", "rad", "g", "-"] = "mm"

    @model_validator(mode="after")
    def _range(self):
        if not self.min <= self.value <= self.max:
            raise ValueError(f"need min <= value <= max, got {self.min} <= {self.value} <= {self.max}")
        return self


TOL_UNIT = 1e-6


class Connector(_Model):
    """ArtiCAD c = (n, o, z, x, l), in the owner's frame, mm. z and x are unit length and orthogonal."""
    name: str
    o: Vec3
    z: Vec3
    x: Vec3
    label: str

    _name = field_validator("name")(_check_name)

    @model_validator(mode="after")
    def _frame(self):
        for k in ("z", "x"):
            n = math.sqrt(sum(c * c for c in getattr(self, k)))
            if abs(n - 1.0) > TOL_UNIT:
                raise ValueError(f"connector {self.name}: |{k}| = {n:.6g}, must be 1")
        dot = sum(a * b for a, b in zip(self.z, self.x))
        if abs(dot) > TOL_UNIT:
            raise ValueError(f"connector {self.name}: z . x = {dot:.6g}, must be 0")
        return self


class ComponentUse(_Model):
    """A purchased part placed in the assembly: card instance fixed to a part connector."""
    id: str
    card: str                       # "servo/mg996r"
    attach_to: str                  # "<part>.<connector>"; the card's mount connector coincides with it

    _id = field_validator("id")(_check_name)

    @field_validator("attach_to")
    @classmethod
    def _ref(cls, v):
        split_ref(v)
        return v


class PartSpec(_Model):
    name: str
    material: Literal["PETG", "PLA"] = "PETG"
    intent: str
    connectors: list[Connector] = Field(default_factory=list)
    bbox_mm: Vec3 | None = None     # rough intended size; L3 checks the built part within ±20 %

    _name = field_validator("name")(_check_part_name)

    @field_validator("connectors")
    @classmethod
    def _unique(cls, v):
        names = [c.name for c in v]
        dup = {n for n in names if names.count(n) > 1}
        if dup:
            raise ValueError(f"duplicate connector names: {sorted(dup)}")
        return v


class Limits(_Model):
    """Revolute: rad. Slider: mm. effort/velocity 'from_card' needs a driver."""
    lower: float
    upper: float
    effort: float | Literal["from_card"] = "from_card"
    velocity: float | Literal["from_card"] = "from_card"

    @model_validator(mode="after")
    def _order(self):
        if self.lower > self.upper:
            raise ValueError(f"lower {self.lower} > upper {self.upper}")
        return self


class Mimic(_Model):
    """Coupled joint (gears / rack_pinion): q = multiplier * q_joint + offset; URDF <mimic>."""
    joint: str
    multiplier: float = 1.0
    offset: float = 0.0


JointType = Literal["fixed", "revolute", "continuous", "slider", "cylindrical", "ball", "gears", "rack_pinion"]
MOVING = {"revolute", "continuous", "slider", "cylindrical"}  # 1-DOF types counted by the plan DOF check


class Joint(_Model):
    name: str
    type: JointType
    parent: str                     # "<part or component id>.<connector>"
    child: str
    driver: str | None = None       # component id of the actuator
    limits: Limits | None = None
    servos_on_axis: int = Field(default=1, ge=1)
    mimic: Mimic | None = None

    _name = field_validator("name")(_check_name)

    @field_validator("parent", "child")
    @classmethod
    def _ref(cls, v):
        split_ref(v)
        return v

    @model_validator(mode="after")
    def _consistency(self):
        if self.type in ("revolute", "slider") and self.limits is None:
            raise ValueError(f"joint {self.name}: {self.type} needs limits")
        if self.type == "fixed" and (self.driver or self.limits):
            raise ValueError(f"joint {self.name}: fixed joints take no driver or limits")
        lim = self.limits
        if lim and self.driver is None and "from_card" in (lim.effort, lim.velocity):
            raise ValueError(f"joint {self.name}: effort/velocity 'from_card' needs a driver; give numbers")
        if self.type in ("gears", "rack_pinion") and self.mimic is None:
            raise ValueError(f"joint {self.name}: coupled joints need mimic")
        return self


class Derived(_Model):
    """Part built as a rigid SE(3) transform of another (mirror / pattern)."""
    name: str
    source: str
    op: Literal["mirror", "pattern"]
    plane: Literal["XY", "XZ", "YZ"] | None = None

    _name = field_validator("name")(_check_name)


def _check_owner(v: str) -> str:
    if "." in v:
        raise ValueError(f"{v!r}: name an owner (part or component id), not a connector")
    return v


class ExpectContact(_Model):
    """At rest, owners a and b touch: closest distance <= tol_mm (an overlap counts as contact). With near =
    "<owner>.<connector>", a's material within radius_mm of that connector's origin must touch b."""
    expect: Literal["contact"]
    a: str
    b: str
    tol_mm: float = Field(default=0.5, ge=0)
    near: str | None = None
    radius_mm: float = Field(default=15.0, gt=0)

    _owners = field_validator("a", "b")(_check_owner)

    @field_validator("near")
    @classmethod
    def _near(cls, v):
        if v is not None:
            split_ref(v)
        return v


class ExpectClearance(_Model):
    """Owners a and b keep >= min_mm apart: over `samples` values of over_joint (others at rest), or over the
    whole L5 sweep when over_joint is omitted."""
    expect: Literal["clearance"]
    a: str
    b: str
    min_mm: float = Field(default=1.0, gt=0)
    over_joint: str | None = None
    samples: int = Field(default=9, ge=2, le=25)

    _owners = field_validator("a", "b")(_check_owner)


class ExpectReach(_Model):
    """Reach of `tool` ("<owner>.<connector>", default: the plan's tool connector) compared with value_mm."""
    expect: Literal["reach"]
    op: Literal[">=", "<="] = ">="
    value_mm: float = Field(gt=0)
    tool: str | None = None

    @field_validator("tool")
    @classmethod
    def _tool(cls, v):
        if v is not None:
            split_ref(v)
        return v


class AllowOverlap(_Model):
    """Exempt the pair from L5 interference (press fit, seated component, cable); the reason is required."""
    expect: Literal["allow_overlap"]
    a: str
    b: str
    reason: str = Field(min_length=3)

    _owners = field_validator("a", "b")(_check_owner)


AssemblyTest = Annotated[ExpectContact | ExpectClearance | ExpectReach | AllowOverlap, Field(discriminator="expect")]


class AssemblyPlan(_Model):
    units: Literal["mm"] = "mm"
    ground: str
    requirements: Spec
    params: dict[str, Param] = Field(default_factory=dict)
    components: list[ComponentUse] = Field(default_factory=list)
    parts: list[PartSpec]
    joints: list[Joint] = Field(default_factory=list)
    derived: list[Derived] = Field(default_factory=list)
    dof: int = Field(ge=0)
    allow_overlap: list[tuple[str, str]] = Field(default_factory=list)  # owner pairs exempt from L5 interference
    tests: list[AssemblyTest] = Field(default_factory=list)            # authored L5 tests (run_tests, P2 §3.2)

    @field_validator("params")
    @classmethod
    def _param_names(cls, v):
        for k in v:
            _check_name(k)
        return v

    @model_validator(mode="after")
    def _unique_names(self):
        owners = [p.name for p in self.parts] + [c.id for c in self.components] + [d.name for d in self.derived]
        dup = {n for n in owners if owners.count(n) > 1}
        if dup:
            raise ValueError(f"part/component names must be unique: {sorted(dup)}")
        joints = [j.name for j in self.joints]
        dup = {n for n in joints if joints.count(n) > 1}
        if dup:
            raise ValueError(f"duplicate joint names: {sorted(dup)}")
        return self

    def overlap_pairs(self) -> list[tuple[str, str]]:
        """allow_overlap pairs plus the authored allow_overlap tests."""
        return list(self.allow_overlap) + [(t.a, t.b) for t in self.tests if t.expect == "allow_overlap"]

    def part(self, name: str) -> PartSpec | None:
        return next((p for p in self.parts if p.name == name), None)
