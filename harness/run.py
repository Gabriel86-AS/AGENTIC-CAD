"""Orchestrator side of the worker protocol: write a request, run freecadcmd, read the response.

One worker process per request. On timeout the whole process tree is killed and a
`failure` signal is returned instead of a response from the worker.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from harness.protocol import PROTOCOL_VERSION, Signal, WorkerResponse

FREECADCMD = Path(os.environ.get("AGENTICCAD_FREECADCMD", r"C:/Program Files/FreeCAD 1.1/bin/freecadcmd.exe"))
WORKER = Path(__file__).resolve().parent / "worker.py"
DEFAULT_TIMEOUT_S = 60.0


def _next_index(io_dir: Path) -> int:
    return sum(1 for _ in io_dir.glob("req_*.json"))


def _kill_tree(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()
    proc.wait()


def _harness_failure(op: str | None, cause: str, location: str, elapsed: float) -> WorkerResponse:
    return WorkerResponse(
        ok=False,
        op=op,
        signals=[Signal(level="failure", gate="harness", cause=cause, location=location)],
        elapsed_s=round(elapsed, 3),
    )


def _log_tail(log_path: Path, n: int = 40) -> str:
    try:
        return "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except OSError:
        return ""


def run_request(request: dict, io_dir: Path, timeout_s: float | None = None) -> WorkerResponse:
    """Run one request in a fresh freecadcmd process and return its parsed response."""
    io_dir = Path(io_dir).resolve()
    io_dir.mkdir(parents=True, exist_ok=True)
    n = _next_index(io_dir)
    req_path = io_dir / f"req_{n}.json"
    res_path = io_dir / f"res_{n}.json"
    log_path = io_dir / f"log_{n}.txt"
    timeout = timeout_s if timeout_s is not None else float(request.get("timeout_s", DEFAULT_TIMEOUT_S))

    req_path.write_text(json.dumps({"protocol": PROTOCOL_VERSION, **request, "response": str(res_path)}, indent=2), encoding="utf-8")
    env = {**os.environ, "AGENTICCAD_REQUEST": str(req_path)}

    t0 = time.monotonic()
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [str(FREECADCMD), str(WORKER)], cwd=io_dir, env=env, stdout=log, stderr=subprocess.STDOUT
        )
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            return _harness_failure(
                request.get("op"), f"timeout after {timeout}s; worker killed", _log_tail(log_path), time.monotonic() - t0
            )

    if not res_path.exists():
        return _harness_failure(
            request.get("op"),
            f"worker exited (code {proc.returncode}) without a response",
            _log_tail(log_path),
            time.monotonic() - t0,
        )
    return WorkerResponse.model_validate_json(res_path.read_text(encoding="utf-8"))
