# Environment facts (T0.1)

Verified 2026-10-09 on the dev host.

| Item | Value |
|---|---|
| OS | Windows 11 Home 10.0.26200 |
| FreeCAD | 1.1.4, build `20260928 (Git shallow)` |
| freecadcmd | `C:/Program Files/FreeCAD 1.1/bin/freecadcmd.exe` |
| FreeCAD Python | 3.11.14 (conda-forge build, MSC v.1944, AMD64) |
| `FreeCAD.GuiUp` in freecadcmd | `0` |

## Headless imports (`freecadcmd -c`)

| Module | Result |
|---|---|
| `Part` | ok |
| `Sketcher` | ok |
| `PartDesign` | ok |
| `Spreadsheet` | ok |
| `Assembly` | ok |
| `pivy`, `pivy.coin` | ok |
| `UtilsAssembly` | ok |
| `JointObject` | ok |
| `Mesh` | ok |
| `Import` | ok |

Import success does not yet prove that Assembly joints solve headless; that is spike S1.

## Orchestrator env (T0.2)

`pyproject.toml` + `uv.lock`; `uv sync` creates `.venv/` with uv-managed CPython 3.12.15.

- `python-preference = "only-managed"` is set because uv otherwise picks LibreOffice's bundled
  3.12 (`C:\Program Files\LibreOffice\program\python-core-3.12.14`), which cannot host a venv.
- All imports ok: anthropic 1.13.0, claude-agent-sdk 0.2.165, pydantic 2.14.0, trimesh 5.1.1,
  manifold3d, pyvista 0.49.0, lib3mf, yourdfpy 0.0.60, lxml 6.1.3, pyyaml 6.0.3.
- **pinocchio: no Windows wheel** (`pin` depends on `cmeel-assimp`, Linux/macOS wheels only).
  ⇒ run pinocchio checks in WSL Ubuntu via a CLI wrapper (P0 risk table).

## Worker protocol (T0.3)

`harness/run.py` (orchestrator) ↔ `harness/worker.py` (inside freecadcmd); tests in `tests/test_worker_protocol.py`.
freecadcmd facts it relies on:
- extra CLI args are opened as documents ⇒ request path via `AGENTICCAD_REQUEST`;
- a script runs with `__name__` = file stem, not `"__main__"`;
- exit code is 0 even when the script raises ⇒ the response file is the only result channel;
- cold start ≈ 1 s per request; timeout kill uses `taskkill /F /T` (no orphans observed).
