"""Component cards: load a card and resolve parameter references.

Pure Python (3.11-compatible): used by the orchestrator and inside freecadcmd by generators.
In `connectors` and `interfaces`, a value may be a number, the name of an entry in `params_mm`, or
a small expression over those names: + - * / and parentheses, e.g. "-hole_pitch_x / 2" or
"spline_top + horn_top_above_tip - horn_disc_t". `resolve()` turns those into numbers, so
`params_mm` is the single source of every dimension; `evaluate()` lets the SDK and generators
build the same expression over their own Params aliases. Other strings ("M4", "+x", "rect")
pass through unchanged.
"""
import ast
import operator
from pathlib import Path

import yaml

COMPONENTS = Path(__file__).resolve().parent

_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


def card_path(card_id: str) -> Path:
    """'servo/mg996r' -> components/servo/mg996r.yaml"""
    return COMPONENTS / f"{card_id}.yaml"


def card_ids() -> list[str]:
    """Every card in the library, e.g. ['electronics/esp32_devkit', ..., 'servo/mg996r']."""
    return sorted(p.relative_to(COMPONENTS).with_suffix("").as_posix() for p in COMPONENTS.glob("*/*.yaml"))


def load_card(card_id_or_path) -> dict:
    p = Path(card_id_or_path)
    if p.suffix != ".yaml":
        p = card_path(str(card_id_or_path))
    card = yaml.safe_load(p.read_text(encoding="utf-8"))
    card["_path"] = str(p)
    return card


def _parse(card: dict, s: str):
    """AST of `s` if it is an expression whose names are all params_mm entries, else None."""
    try:
        tree = ast.parse(s.strip(), mode="eval").body
    except SyntaxError:
        return None
    params = card.get("params_mm", {})
    names = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id not in params:
                return None
            names = True
        elif not isinstance(node, (ast.BinOp, ast.UnaryOp, ast.Constant, ast.Load, ast.USub, ast.UAdd,
                                   *_BINOPS)):
            return None
        elif isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            return None
    return tree if names else None


def is_expr(card: dict, value) -> bool:
    return isinstance(value, str) and _parse(card, value) is not None


def evaluate(card: dict, s: str, name):
    """Evaluate card expression `s` with `name(param) -> operand`; literals stay floats. Operands
    only need + - * / with floats and unary minus (floats, the SDK's Q, ...)."""
    tree = _parse(card, s)
    if tree is None:
        raise ValueError("%s: %r is not an expression over params_mm" % (card.get("id"), s))

    def ev(n):
        if isinstance(n, ast.Name):
            return name(n.id)
        if isinstance(n, ast.Constant):
            return float(n.value)
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        return _BINOPS[type(n.op)](ev(n.left), ev(n.right))

    return ev(tree)


def resolve(card: dict, value):
    """Replace param names and param expressions (str) with their mm values, recursively."""
    params = card.get("params_mm", {})
    if isinstance(value, str):
        if _parse(card, value) is not None:
            return float(evaluate(card, value, lambda p: float(params[p])))
        return value
    if isinstance(value, list):
        return [resolve(card, v) for v in value]
    if isinstance(value, dict):
        return {k: resolve(card, v) for k, v in value.items()}
    return value


def freecad_expr(card: dict, value, prefix: str = "Params.") -> str:
    """FreeCAD expression for a card value: params become `<prefix><name>`, literals get ` mm`."""
    if isinstance(value, (int, float)):
        return "%r mm" % float(value)
    return _render(_parse(card, value), prefix)


def _render(n, prefix):
    if isinstance(n, ast.Name):
        return prefix + n.id
    if isinstance(n, ast.Constant):
        return "%r mm" % float(n.value)
    if isinstance(n, ast.UnaryOp):
        return "(-%s)" % _render(n.operand, prefix) if isinstance(n.op, ast.USub) else _render(n.operand, prefix)
    op = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}[type(n.op)]
    left, right = _render(n.left, prefix), _render(n.right, prefix)
    if isinstance(n.op, (ast.Mult, ast.Div)) and isinstance(n.right, ast.Constant):
        right = repr(float(n.right.value))  # unitless factor
    if isinstance(n.op, ast.Mult) and isinstance(n.left, ast.Constant):
        left = repr(float(n.left.value))
    return "(%s %s %s)" % (left, op, right)


def connectors(card: dict) -> dict:
    """name -> {"o": [x,y,z] mm, "z": [...], "x": [...], "label": str}, params resolved."""
    return resolve(card, card.get("connectors", {}))


def unresolved(card: dict) -> list[str]:
    """Lint: strings in connector origins and interface dimensions that look like params but aren't."""
    bad = []
    keys = {"o", "size", "centre_xy", "box_min", "box_max", "pitch", "top_z", "bottom_z", "d", "h",
            "slot_len", "slot_w", "disc_d", "disc_t", "bolt_circle_d", "access_d"}

    def walk(v, key, where):
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, k, "%s.%s" % (where, k))
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, key, "%s[%d]" % (where, i))
        elif isinstance(v, str) and key in keys and not is_expr(card, v):
            bad.append("%s = %r" % (where, v))

    walk(card.get("connectors", {}), None, "connectors")
    walk(card.get("interfaces", {}), None, "interfaces")
    return bad
