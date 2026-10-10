"""Domain packs (plan §1.4, T1.10): data side, no FreeCAD. The composites run in tests/test_sdk.py."""
import ast

import packs
from schemas.plan import Spec


def test_manipulator_pack_is_discovered():
    assert "manipulator" in packs.available()
    assert packs.meta("manipulator")["id"] == "manipulator"


def test_declared_composites_exist():
    for pack in packs.available():
        path = packs.ROOT / pack / "composites.py"
        defined = {n.name for n in ast.parse(path.read_text(encoding="utf-8")).body if isinstance(n, ast.FunctionDef)} \
            if path.is_file() else set()
        assert set(packs.meta(pack).get("composites", [])) <= defined, pack


def test_bench_prompts_have_valid_specs():
    items = packs.bench("manipulator")
    assert [b["id"] for b in items] == ["B1-1", "B1-2", "B1-3", "B1-4", "B1-5"]
    for b in items:
        spec = Spec.model_validate(b["spec"])
        assert spec.pack == "manipulator" and spec.dof == 2 and b["prompt"]


def test_knowledge_names_real_calls():
    text = packs.knowledge("manipulator")
    assert "servo_u_bracket" in text and "mate_to" in text
    assert packs.knowledge("no_such_pack") == ""
