"""Component cards: load a card and resolve parameter references.

Pure Python (3.11-compatible): used by the orchestrator and inside freecadcmd by generators.
In `connectors` and `interfaces`, a value may be a number or the name of an entry in
`params_mm`, optionally with a leading "-" (e.g. "-hole_pitch_x"). `resolve()` turns those
names into numbers, so `params_mm` is the single source of every dimension.
"""
from pathlib import Path

import yaml

COMPONENTS = Path(__file__).resolve().parent


def card_path(card_id: str) -> Path:
    """'servo/mg996r' -> components/servo/mg996r.yaml"""
    return COMPONENTS / f"{card_id}.yaml"


def load_card(card_id_or_path) -> dict:
    p = Path(card_id_or_path)
    if p.suffix != ".yaml":
        p = card_path(str(card_id_or_path))
    card = yaml.safe_load(p.read_text(encoding="utf-8"))
    card["_path"] = str(p)
    return card


def resolve(card: dict, value):
    """Replace param names (str) with their mm values, recursively."""
    params = card.get("params_mm", {})
    if isinstance(value, str):
        sign, name = (-1.0, value[1:]) if value.startswith("-") else (1.0, value)
        if name in params:
            return sign * float(params[name])
        return value
    if isinstance(value, list):
        return [resolve(card, v) for v in value]
    if isinstance(value, dict):
        return {k: resolve(card, v) for k, v in value.items()}
    return value


def connectors(card: dict) -> dict:
    """name -> {"o": [x,y,z] mm, "z": [...], "x": [...], "label": str}, params resolved."""
    return resolve(card, card.get("connectors", {}))
