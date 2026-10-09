"""Print profiles and the material table. Pure Python 3.11 (the SDK reads these inside freecadcmd).

Validated on the orchestrator side by schemas.profile.PrintProfile.
"""
from pathlib import Path

import yaml

PROFILES = Path(__file__).resolve().parent


def load_profile(name_or_path="generic_fdm") -> dict:
    p = Path(name_or_path)
    if p.suffix != ".yaml":
        p = PROFILES / f"{name_or_path}.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def load_materials() -> dict:
    return yaml.safe_load((PROFILES / "materials.yaml").read_text(encoding="utf-8"))


def hole_diameter(profile: dict, d: float, fit: str, horizontal: bool = False) -> float:
    """Printed hole diameter for nominal `d` and a `fits` entry, or a screw fit like 'clearance_m3' / 'tap_m3'."""
    kind, _, thread = fit.partition("_")
    if thread:
        size = profile["screws"][thread.upper()][kind]
    else:
        size = d + profile["fits"][fit]
    return size + (profile["horizontal_hole_extra_mm"] if horizontal else 0.0)
