"""Orchestrator-side schema for worker responses. The worker emits plain JSON matching this.

Protocol v1 is frozen at P0 exit (docs/worker_protocol.md). Unknown fields are rejected, so any
change needs PROTOCOL_VERSION bumped here and in harness/worker.py.
"""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = 1


class Signal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Literal["failure", "warning", "info"]
    gate: str
    cause: str
    location: str = ""
    action: str = ""


class WorkerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: int = PROTOCOL_VERSION
    ok: bool
    op: str | None = None
    signals: list[Signal] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    freecad_version: str | None = None
    elapsed_s: float = 0.0
