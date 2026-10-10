"""Job trace (P1 exit: tokens + cost per job): jobs/<id>/trace.jsonl, one TraceEvent per line.

Per agent session: one event per tool call (tool, input/output hashes) and one session event with the
tokens and cost the backend reported (Agent SDK: usage + total_cost_usd; Claude API: tokens only).
"""
import hashlib
import json
import threading
import time
from pathlib import Path

from llm.backend import RunResult
from schemas.signal import TraceEvent


def digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def tokens(usage: dict) -> tuple[int, int, float]:
    """(tokens_in, tokens_out, cost_usd) from either backend's RunResult.usage."""
    u = usage.get("usage") if isinstance(usage.get("usage"), dict) else usage
    u = u or {}
    tin = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    return tin, int(u.get("output_tokens") or 0), float(usage.get("total_cost_usd") or 0.0)


class Trace:
    def __init__(self, path: Path | None):
        self.path = Path(path) if path else None
        self.events: list[TraceEvent] = []
        self._lock = threading.Lock()

    def add(self, ev: TraceEvent):
        with self._lock:
            self.events.append(ev)
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(ev.model_dump_json() + "\n")

    def session(self, agent: str, res: RunResult, prompt: str = ""):
        for c in res.tool_calls:
            self.add(TraceEvent(t=time.time(), agent=agent, model=res.model, tool=c.name,
                                input_hash=digest(c.input), output_hash=digest(c.output)))
        tin, tout, cost = tokens(res.usage)
        self.add(TraceEvent(t=time.time(), agent=agent, model=res.model, input_hash=digest(prompt),
                            output_hash=digest(res.final_text), tokens_in=tin, tokens_out=tout, cost_usd=cost))

    def totals(self) -> dict:
        out = {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "sessions": 0, "by_agent": {}}
        for e in self.events:
            if e.tool is not None:
                continue
            out["sessions"] += 1
            a = out["by_agent"].setdefault(e.agent, {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "sessions": 0})
            for d in (out, a):
                d["tokens_in"] += e.tokens_in
                d["tokens_out"] += e.tokens_out
                d["cost_usd"] = round(d["cost_usd"] + e.cost_usd, 6)
            a["sessions"] += 1
        return out
