"""FreeCAD-side worker. Runs inside freecadcmd.exe (Python 3.11), never in the orchestrator env.

Invocation (see harness/run.py):
    AGENTICCAD_REQUEST=<path to req_<n>.json> freecadcmd.exe harness/worker.py

freecadcmd quirks this file relies on (verified 2026-10-09, FreeCAD 1.1.4):
- extra CLI args are opened as documents, so the request path comes from an env var;
- the script runs with __name__ == <file stem>, not "__main__";
- the process exit code is 0 even on errors, so the response file is the only result channel.

The worker always writes a response, even on an exception. Plain JSON only (no pydantic here).
Protocol v1 is frozen (docs/worker_protocol.md); bump PROTOCOL_VERSION on both sides to change it.
"""
import json
import os
import sys
import time
import traceback

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import FreeCAD  # noqa: E402

PROTOCOL_VERSION = 1  # must equal harness/protocol.py


def _freecad_version():
    v = FreeCAD.Version()
    return "%s.%s.%s" % (v[0], v[1], v[2])


def _signal(level, gate, cause, location="", action=""):
    return {"level": level, "gate": gate, "cause": cause, "location": location, "action": action}


def _empty_response(op):
    return {
        "protocol": PROTOCOL_VERSION,
        "ok": False,
        "op": op,
        "signals": [],
        "artifacts": {},
        "metrics": {},
        "result": None,
        "freecad_version": _freecad_version(),
        "elapsed_s": 0.0,
    }


def op_ping(req, res):
    res["result"] = {"python": sys.version.split()[0], "gui_up": bool(FreeCAD.GuiUp)}
    res["ok"] = True


def op_run_script(req, res):
    """Exec a Python file. The script fills the `result` dict it is given; spikes use this."""
    path = req["script"]
    result = {}
    namespace = {"__name__": "__agenticcad_script__", "__file__": path, "result": result, "request": req}
    with open(path, "r", encoding="utf-8") as f:
        code = compile(f.read(), path, "exec")
    exec(code, namespace)
    res["result"] = result
    res["ok"] = True


def op_compile_part(req, res):
    """Run parts/<name>.py through the SDK and report gates L1-L3 (harness/part_ops.py)."""
    from harness.part_ops import compile_part
    compile_part(req, res)


def op_probe_part(req, res):
    """Read-only measurement snippet against a compiled .FCStd (harness/part_ops.py)."""
    from harness.part_ops import probe_part
    probe_part(req, res)


def op_build_assembly(req, res):
    """Plan + compiled parts -> assembly.FCStd with Assembly WB joints, posed by our FK (assembly/writer.py)."""
    from assembly.writer import build_assembly
    build_assembly(req, res)


def op_sweep_assembly(req, res):
    """Interference over joint poses + owner mass properties (validate/sweep.py, gates L5/L6)."""
    from validate.sweep import sweep_assembly
    sweep_assembly(req, res)


def op_mesh_parts(req, res):
    """STL per part in its own frame (validate/sweep.py; L7 and the exporter read them)."""
    from validate.sweep import mesh_parts
    mesh_parts(req, res)


OPS = {
    "ping": op_ping,
    "run_script": op_run_script,
    "compile_part": op_compile_part,
    "probe_part": op_probe_part,
    "build_assembly": op_build_assembly,
    "sweep_assembly": op_sweep_assembly,
    "mesh_parts": op_mesh_parts,
}


def main():
    t0 = time.time()
    req_path = os.environ.get("AGENTICCAD_REQUEST")
    if not req_path:
        sys.stderr.write("AGENTICCAD_REQUEST not set\n")
        return
    res_path = req_path.replace("req_", "res_", 1) if "req_" in os.path.basename(req_path) else req_path + ".res.json"
    res = _empty_response(None)
    try:
        with open(req_path, "r", encoding="utf-8") as f:
            req = json.load(f)
        res_path = req.get("response", res_path)
        res["op"] = req.get("op")
        handler = OPS.get(req.get("op"))
        if req.get("protocol") != PROTOCOL_VERSION:
            res["signals"].append(_signal("failure", "harness", "protocol mismatch: request %r, worker %d"
                                          % (req.get("protocol"), PROTOCOL_VERSION)))
        elif handler is None:
            res["signals"].append(_signal("failure", "harness", "unknown op %r" % req.get("op")))
        else:
            handler(req, res)
    except Exception as e:
        res["ok"] = False
        res["signals"].append(
            _signal("failure", "harness", "%s: %s" % (type(e).__name__, e), location=traceback.format_exc())
        )
    res["elapsed_s"] = round(time.time() - t0, 3)
    tmp = res_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2, default=str)
    os.replace(tmp, res_path)


main()
