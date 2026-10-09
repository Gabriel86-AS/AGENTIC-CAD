"""S10 orchestrator side: run the worker cases and check the harness turns hang/crash into failures."""
import time
from pathlib import Path

from harness.run import run_request


def drive(script: Path, out_dir: Path) -> dict:
    io = out_dir / "io"
    rows = {}
    for case, timeout in (("inproc", 60), ("hang", 10), ("crash", 30)):
        t0 = time.monotonic()
        res = run_request({"op": "run_script", "script": str(script), "out_dir": str(out_dir), "case": case}, io, timeout_s=timeout)
        rows[case] = {"ok": res.ok, "signals": [s.model_dump() for s in res.signals],
                      "result": res.result, "wall_s": round(time.monotonic() - t0, 2)}
    causes = {k: (v["signals"][0]["cause"] if v["signals"] else "") for k, v in rows.items()}
    rows["pass"] = bool(
        rows["inproc"]["ok"] and (rows["inproc"]["result"] or {}).get("pass")
        and not rows["hang"]["ok"] and causes["hang"].startswith("timeout")
        and not rows["crash"]["ok"] and "without a response" in causes["crash"]
    )
    return {"ok": True, "op": "driver", "signals": [], "result": rows}
