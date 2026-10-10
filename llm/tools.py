"""Tools defined once (plain functions + JSON schema), adapted by each LLMBackend.

PartWorkspace is the restricted action space of one part agent (plan §3.4):
  read_file     own part file + read-only roots (SDK docs, cards, plan excerpt)
  write_file    ONLY parts/<own>.py; anything else raises ToolDenied
  compile_part  gates L0-L4 on parts/<own>.py (harness/tools.py) and returns formatted feedback
  probe_part    read-only measurement snippet against the last compiled model
The path checks live here, not in the backends, so both backends enforce the same rule.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from harness import tools as harness_tools
from schemas.plan import AssemblyPlan

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
    timeout_s: float = 180.0   # compile_part includes L4 flex (up to 30 recomputes)
    plan: AssemblyPlan | None = None  # params, components and this part's PartSpec for the gates
    max_compiles: int | None = None   # K (plan §4.4): compile_part calls allowed in this session
    log: list[dict] = field(default_factory=list)  # every call, allowed or not (trace + tests)
    compiles: int = 0
    last_compile_ok: bool = False

    def __post_init__(self):
        self.root = Path(self.root).resolve()
        self.own = (self.root / "parts" / f"{self.part}.py").resolve()
        self.read_roots = [Path(r).resolve() for r in self.read_roots]
        self.io = self.root / "io" / self.part
        self.fcstd = self.root / "build" / f"{self.part}.FCStd"

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
        if self.max_compiles is not None and self.compiles >= self.max_compiles:
            self._record("compile_part", args, "denied")
            raise ToolDenied(f"compile budget of {self.max_compiles} used up; stop and summarise what still fails")
        self.compiles += 1
        res = harness_tools.compile_part(self.own, self.io, out=self.fcstd, part=self.part, plan=self.plan,
                                         timeout_s=self.timeout_s)
        self.last_compile_ok = res.ok
        self._record("compile_part", args, "ok" if res.ok else "failure")
        sk = (res.report or {}).get("sketches") or {}
        self.log[-1].update(gates=sorted({s.gate for s in res.signals if s.level == "failure"}),
                            n_sketches=len(sk), n_dof0=sum(1 for s in sk.values() if s.get("dof") == 0))
        out = res.for_agent()
        if self.max_compiles is not None:
            out["compiles_left"] = self.max_compiles - self.compiles
        return out

    def probe_part(self, args: dict) -> dict:
        if not self.fcstd.exists():
            self._record("probe_part", args, "denied")
            raise ToolDenied("no compiled model to probe; call compile_part first (it must get past L0/L1)")
        res = harness_tools.probe_part(self.fcstd, args["snippet"], self.io)
        self._record("probe_part", args, "ok" if res.ok else "failure")
        return res.for_agent()

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
            ToolSpec("compile_part", f"Check and build parts/{self.part}.py: static check (L0), FreeCAD build (L1), "
                                     "sketches fully constrained and bound to Params (L2), one valid solid and plan "
                                     "connectors at their declared poses (L3). Returns PASS/FAIL feedback with "
                                     "cause, location and fix for each signal, plus volume, bbox, connectors, params.",
                     {"type": "object", "properties": {}}, self.compile_part),
            ToolSpec("probe_part", "Measure the last compiled model (read-only). `snippet` is Python using the "
                                   "`probe` object and `math`; the value of its last expression is returned. "
                                   "probe: bbox(), volume(), area(), com(), connectors(), connector(name), "
                                   "params(), param(name), sketches(), features(), inside(p), distance(p), "
                                   "segments(p0, p1), thickness(p, direction), help(). Points in mm, part frame.",
                     {"type": "object", "properties": {"snippet": {"type": "string"}}, "required": ["snippet"]},
                     self.probe_part),
        ]
