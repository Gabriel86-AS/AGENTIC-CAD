"""Orchestrator-side structured feedback (plan §4.3) and trace events.

The worker wire format (harness.protocol.Signal, frozen v1) has `level: info` and no evidence; it is
converted here with `from_worker` (info -> note). Gates on the orchestrator side emit this Signal.
"""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from harness.protocol import Signal as WorkerSignal


class Signal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Literal["failure", "warning", "note"]
    gate: str                       # "L0".."L7", "D1".., "plan", "harness"
    cause: str
    location: str = ""              # e.g. "parts/upper_arm.py:12", "plan.params.upper_arm_len"
    action: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_worker(cls, s: WorkerSignal) -> "Signal":
        return cls(level="note" if s.level == "info" else s.level, gate=s.gate,
                   cause=s.cause, location=s.location, action=s.action)


class TraceEvent(BaseModel):
    """One line of jobs/<id>/trace.jsonl. Hashes are sha256 of the canonical JSON input/output."""
    model_config = ConfigDict(extra="forbid")

    t: float                        # unix time
    agent: str
    model: str | None = None
    tool: str | None = None
    input_hash: str = ""
    output_hash: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    freecad_version: str | None = None
