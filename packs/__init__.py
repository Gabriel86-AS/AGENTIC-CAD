"""Domain packs (plan §1.4): composites, knowledge docs and bench prompts for one domain.

The core never names a pack: the SDK imports every `packs/<id>/composites.py` it finds (they register
methods on sdk.Part inside freecadcmd), and the orchestrator reads the rest as data through this module
(pure Python, both interpreters). A pack is a directory with:

    __init__.py      PACK = {"id", "summary", "composites": [names]}   (no FreeCAD imports)
    composites.py    optional; runs inside freecadcmd
    knowledge.md     curated design notes, put straight into the design agents' prompts (P1)
    bench.yaml       benchmark prompts: [{id, prompt, spec}]
"""
import importlib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent


def available() -> list[str]:
    return sorted(p.name for p in ROOT.iterdir() if (p / "__init__.py").is_file())


def meta(pack: str) -> dict:
    if pack not in available():
        raise KeyError("no pack %r; packs: %s" % (pack, available()))
    return importlib.import_module("packs." + pack).PACK


def knowledge(pack: str) -> str:
    path = ROOT / pack / "knowledge.md"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def bench(pack: str) -> list[dict]:
    path = ROOT / pack / "bench.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else []


def load_composites() -> list[str]:
    """Import every pack's composites module (inside freecadcmd); returns the pack ids loaded."""
    loaded = []
    for pack in available():
        if (ROOT / pack / "composites.py").is_file():
            importlib.import_module("packs.%s.composites" % pack)
            loaded.append(pack)
    return loaded
