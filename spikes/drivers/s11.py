"""S11 is orchestrator-side (LLM sessions): delegate to spikes/s11_llm.py instead of the worker."""
from pathlib import Path

from spikes import s11_llm


def drive(script: Path, out_dir: Path) -> dict:
    out = s11_llm.main([])
    statuses = {k: out[k]["status"] for k in ("agent_sdk", "claude_api")}
    return {"ok": True, "op": "driver", "signals": [],
            "result": {"statuses": statuses, "pass": statuses["agent_sdk"] == "pass"
                       and statuses["claude_api"] in ("pass", "skipped (no API key)")}}
