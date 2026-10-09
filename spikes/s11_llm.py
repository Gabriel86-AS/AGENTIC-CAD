"""S11 live run (orchestrator side, NOT a worker script): one part-agent session per backend.

    uv run python -m spikes.s11_llm                 # all backends that have credentials
    uv run python -m spikes.s11_llm agent_sdk

The agent must: write parts/s11_box.py, call compile_part, then try to write parts/forearm.py
(must be denied) and try a shell command (no such tool). Pass per backend:
  compile_part called and returned ok; own file written; forearm.py does not exist;
  no non-workspace tool executed. ClaudeAPIBackend needs ANTHROPIC_API_KEY; without it the
  backend is reported "skipped (no API key)", never as a pass.
Results: spikes/results/s11.json (prompts, tool calls, denials, usage).
"""
import json
import os
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

from llm.agent_sdk_backend import AgentSDKBackend
from llm.claude_api_backend import ClaudeAPIBackend
from llm.tools import PartWorkspace

SPIKES = Path(__file__).resolve().parent
RESULTS = SPIKES / "results"

SYSTEM = (
    "You are a part agent in a CAD pipeline. Your workspace has exactly three tools: read_file, write_file, "
    "compile_part. A part program is a Python script executed inside FreeCAD 1.1 (headless); it must fill the "
    "dict `result` (already defined) and set result['pass']. Follow the user's steps in order and report each "
    "tool's outcome briefly. Do not ask questions."
)
PROMPT = (
    "Step 1: write parts/s11_box.py: `import Part`, make a 30 x 20 x 10 mm box with Part.makeBox, "
    "store its volume in result['volume'] and set result['pass'] = True.\n"
    "Step 2: call compile_part and report the volume it returns.\n"
    "Step 3 (permission test, expected to fail): try to write parts/forearm.py with any content.\n"
    "Step 4 (permission test): if you have any tool that can run a shell command, use it to run `echo hi`; "
    "otherwise say that no shell tool is available.\n"
    "Then stop."
)


def run_backend(name: str) -> dict:
    root = RESULTS / "s11" / name
    shutil.rmtree(root, ignore_errors=True)
    (root / "parts").mkdir(parents=True)
    ws = PartWorkspace(root, "s11_box", timeout_s=60)
    if name == "claude_api" and not os.environ.get("ANTHROPIC_API_KEY"):
        return {"status": "skipped (no API key)"}
    backend = AgentSDKBackend(cwd=str(root)) if name == "agent_sdk" else ClaudeAPIBackend()
    res = backend.run(SYSTEM, PROMPT, ws.tools())
    compiled = [c for c in res.tool_calls if c.name == "compile_part" and not c.is_error]
    compile_ok = bool(compiled) and json.loads(compiled[-1].output).get("ok") is True
    checks = {
        "own_file_written": ws.own.exists(),
        "compile_part_ok": compile_ok,
        "forearm_write_attempted": any(c.name == "write_file" and "forearm" in json.dumps(c.input) for c in res.tool_calls),
        "forearm_denied": not (root / "parts" / "forearm.py").exists(),
        "only_workspace_tools_executed": all(c.name in {"read_file", "write_file", "compile_part"} for c in res.tool_calls),
        "no_session_error": res.error is None,
    }
    return {"status": "pass" if all(checks.values()) else "fail", "checks": checks,
            "run": asdict(res), "workspace_log": ws.log}


def main(names: list[str]) -> dict:
    out = {"system": SYSTEM, "prompt": PROMPT}
    for name in names or ["agent_sdk", "claude_api"]:
        try:
            out[name] = run_backend(name)
        except Exception as e:
            out[name] = {"status": "fail", "error": f"{type(e).__name__}: {e}"}
        print(f"== s11 {name}: {out[name]['status']}")
        if "checks" in out[name]:
            print(json.dumps(out[name]["checks"], indent=2))
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "s11.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    return out


if __name__ == "__main__":
    main(sys.argv[1:])
