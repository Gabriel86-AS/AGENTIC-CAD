"""Tools defined once (plain functions + JSON schema), adapted by each LLMBackend.

PartWorkspace is the restricted action space of one part agent (plan §3.4):
  read_file     own part file + read-only roots (SDK docs, cards, plan excerpt)
  write_file    ONLY parts/<own>.py; anything else raises ToolDenied
  compile_part  runs parts/<own>.py in the FreeCAD worker and returns its signals
The path checks live here, not in the backends, so both backends enforce the same rule.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from harness.run import run_request

MAX_WRITE_BYTES = 200_000


class ToolDenied(Exception):
    """A tool call that the workspace policy rejects. Returned to the model as an error result."""


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    fn: Callable[[dict], Any]

    def call(self, args: dict) -> tuple[str, bool]:
        """Run the tool; return (text for the model, is_error). Never raises."""
        try:
            return json.dumps(self.fn(args), default=str), False
        except ToolDenied as e:
            return json.dumps({"error": "denied", "detail": str(e)}), True
        except Exception as e:  # tool bug or bad input: report to the model, keep the session alive
            return json.dumps({"error": type(e).__name__, "detail": str(e)}), True


@dataclass
class PartWorkspace:
    root: Path
    part: str
    read_roots: list[Path] = field(default_factory=list)
    timeout_s: float = 60.0
    log: list[dict] = field(default_factory=list)  # every call, allowed or not (trace + tests)

    def __post_init__(self):
        self.root = Path(self.root).resolve()
        self.own = (self.root / "parts" / f"{self.part}.py").resolve()
        self.read_roots = [Path(r).resolve() for r in self.read_roots]

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        return (p if p.is_absolute() else self.root / p).resolve()

    def _record(self, tool: str, args: dict, outcome: str):
        self.log.append({"tool": tool, "args": {k: (v if k != "content" else f"<{len(v)} chars>") for k, v in args.items()},
                         "outcome": outcome})

    def read_file(self, args: dict) -> dict:
        p = self._resolve(args["path"])
        if p != self.own and not any(p.is_relative_to(r) for r in self.read_roots):
            self._record("read_file", args, "denied")
            raise ToolDenied(f"{args['path']} is outside the readable files of part '{self.part}'")
        self._record("read_file", args, "ok")
        return {"path": str(p.relative_to(self.root)) if p.is_relative_to(self.root) else str(p),
                "content": p.read_text(encoding="utf-8")}

    def write_file(self, args: dict) -> dict:
        p = self._resolve(args["path"])
        if p != self.own:
            self._record("write_file", args, "denied")
            raise ToolDenied(f"only parts/{self.part}.py is writable; got {args['path']}")
        content = args["content"]
        if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
            self._record("write_file", args, "denied")
            raise ToolDenied(f"file larger than {MAX_WRITE_BYTES} bytes")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        self._record("write_file", args, "ok")
        return {"written": f"parts/{self.part}.py", "bytes": len(content.encode("utf-8"))}

    def compile_part(self, args: dict) -> dict:
        if not self.own.exists():
            self._record("compile_part", args, "denied")
            raise ToolDenied(f"parts/{self.part}.py does not exist yet; write it first")
        res = run_request({"op": "run_script", "script": str(self.own), "timeout_s": self.timeout_s},
                          self.root / "io" / self.part, timeout_s=self.timeout_s)
        self._record("compile_part", args, "ok" if res.ok else "failure")
        return {"ok": res.ok, "signals": [s.model_dump() for s in res.signals], "result": res.result,
                "freecad_version": res.freecad_version, "elapsed_s": res.elapsed_s}

    def tools(self) -> list[ToolSpec]:
        return [
            ToolSpec("read_file", "Read a text file: your own part program, SDK docs or component cards.",
                     {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                     self.read_file),
            ToolSpec("write_file", f"Write the full content of your part program parts/{self.part}.py "
                                   "(the only writable file).",
                     {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                      "required": ["path", "content"]},
                     self.write_file),
            ToolSpec("compile_part", f"Run parts/{self.part}.py in the FreeCAD worker and return its result and "
                                     "failure/warning/info signals.",
                     {"type": "object", "properties": {}}, self.compile_part),
        ]
