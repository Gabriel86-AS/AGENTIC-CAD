"""Orchestrator-side schema for worker responses (P0 §5). The worker emits plain JSON matching this."""
from typing import Any, Literal

from pydantic import BaseModel, Field


class Signal(BaseModel):
    level: Literal["failure", "warning", "info"]
    gate: str
    cause: str
    location: str = ""
    action: str = ""


class WorkerResponse(BaseModel):
    ok: bool
    op: str | None = None
    signals: list[Signal] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    freecad_version: str | None = None
    elapsed_s: float = 0.0
