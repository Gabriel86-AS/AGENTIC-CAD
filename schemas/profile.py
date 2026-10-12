"""PrintProfile: shape of profiles/<id>.yaml (plan §4.2)."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ScrewHoles(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clearance: float = Field(gt=0)
    tap: float = Field(gt=0)


class PrintProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    process: Literal["fdm"]
    build_volume_mm: tuple[float, float, float]
    nozzle_mm: float = Field(gt=0)
    layer_mm: float = Field(gt=0)
    line_width_mm: float = Field(gt=0)
    wall_count: int = Field(ge=1)
    infill: float = Field(ge=0, le=1)
    min_wall_mm: float = Field(gt=0)
    min_feature_mm: float = Field(gt=0)
    max_overhang_deg: float = Field(gt=0, lt=90)
    max_bridge_mm: float = Field(ge=0)
    max_support_frac: float = Field(default=0.15, ge=0)
    fits: dict[str, float]
    horizontal_hole_extra_mm: float = Field(ge=0)
    screws: dict[str, ScrewHoles]
