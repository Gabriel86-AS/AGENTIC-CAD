"""Run a spike script inside the FreeCAD worker and save its result.

    uv run python -m spikes.run s01            # runs spikes/s01_*.py
    uv run python -m spikes.run s01 s02 s03

Each spike fills the `result` dict and sets result["pass"]. Results go to spikes/results/<id>.json.
A spike that needs orchestrator-side checks has spikes/drivers/<id>.py with drive(script, out_dir).
"""
import importlib
import json
import sys
from pathlib import Path

from harness.run import run_request

SPIKES = Path(__file__).resolve().parent
RESULTS = SPIKES / "results"


def run_spike(spike_id: str, timeout_s: float = 120) -> dict:
    matches = sorted(SPIKES.glob(f"{spike_id}_*.py"))
    if len(matches) != 1:
        raise SystemExit(f"expected one spikes/{spike_id}_*.py, found {matches}")
    script = matches[0]
    out_dir = RESULTS / spike_id
    if (SPIKES / "drivers" / f"{spike_id}.py").exists():
        # Orchestrator-side driver: issues its own requests (e.g. timeout / crash checks)
        record = importlib.import_module(f"spikes.drivers.{spike_id}").drive(script, out_dir)
    else:
        res = run_request(
            {"op": "run_script", "script": str(script), "out_dir": str(out_dir)}, out_dir / "io", timeout_s=timeout_s
        )
        record = res.model_dump()
    (RESULTS / f"{spike_id}.json").write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    return record


def main(argv: list[str]) -> None:
    RESULTS.mkdir(exist_ok=True)
    for spike_id in argv:
        rec = run_spike(spike_id)
        verdict = (rec["result"] or {}).get("pass") if rec["ok"] else "ERROR"
        print(f"== {spike_id}: {verdict}")
        print(json.dumps(rec["result"] if rec["ok"] else rec["signals"], indent=2, default=str))


if __name__ == "__main__":
    main(sys.argv[1:])
