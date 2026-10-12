"""Write JSON Schema files for the contracts (agents get these in their prompts; tests check they are current)."""
import json
from pathlib import Path

from schemas.plan import AssemblyPlan, Spec
from schemas.profile import PrintProfile
from schemas.review import LookVerdict
from schemas.signal import Signal, TraceEvent

OUT = Path(__file__).resolve().parent / "json"
MODELS = {"spec": Spec, "assembly_plan": AssemblyPlan, "signal": Signal, "trace_event": TraceEvent,
          "print_profile": PrintProfile, "look_verdict": LookVerdict}


def render() -> dict[str, str]:
    return {f"{k}.schema.json": json.dumps(m.model_json_schema(), indent=2) + "\n" for k, m in MODELS.items()}


def main():
    OUT.mkdir(exist_ok=True)
    for name, text in render().items():
        (OUT / name).write_text(text, encoding="utf-8", newline="\n")
        print(OUT / name)


if __name__ == "__main__":
    main()
