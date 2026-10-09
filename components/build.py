"""Build a component card's FreeCAD model in the worker.

    uv run python -m components.build servo/mg996r      # -> build/components/servo_mg996r.FCStd
"""
import json
import sys
from pathlib import Path

from components.card import COMPONENTS, load_card
from harness.protocol import WorkerResponse
from harness.run import run_request

BUILD = COMPONENTS.parent / "build" / "components"


def build_card(card_id: str, out_dir: Path = BUILD, timeout_s: float = 120) -> WorkerResponse:
    card = load_card(card_id)
    if not card.get("model"):
        raise SystemExit(f"{card_id}: card has no generator (model: null)")
    out = Path(out_dir).resolve() / (card_id.replace("/", "_") + ".FCStd")
    return run_request(
        {"op": "run_script", "script": str(COMPONENTS / card["model"]), "card": card["_path"], "out": str(out)},
        Path(out_dir) / "io",
        timeout_s=timeout_s,
    )


if __name__ == "__main__":
    for cid in sys.argv[1:]:
        res = build_card(cid)
        print(f"== {cid}: {'PASS' if res.ok and (res.result or {}).get('pass') else 'FAIL'}")
        print(json.dumps(res.result if res.ok else [s.model_dump() for s in res.signals], indent=2))
