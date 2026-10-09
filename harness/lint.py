"""Gate L0 (P1 §4.4): static checks on agent code before it reaches the FreeCAD worker.

A guardrail, not a sandbox: it keeps part programs on the SDK surface (so the SDK's guarantees hold:
DoF 0, Params-bound dimensions, no face references) and catches obvious escapes early with a
precise location. Process isolation of the worker is the real boundary.
"""
import ast

from schemas.signal import Signal

PROGRAM_IMPORTS = {"agenticcad", "math"}
PROBE_IMPORTS = {"math"}
BANNED_NAMES = {
    "open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars", "getattr", "setattr",
    "delattr", "input", "breakpoint", "exit", "quit", "help", "memoryview", "classmethod", "staticmethod",
    "FreeCAD", "App", "FreeCADGui", "Gui", "Sketcher", "PartDesign", "os", "sys", "subprocess",
}
# Raw FreeCAD handles on SDK objects: using them bypasses the SDK's guarantees.
BANNED_ATTRS = {"doc", "body", "sheet", "container", "obj", "configure", "context"}


def _sig(cause, where, line, action):
    return Signal(level="failure", gate="L0", cause=cause, location="%s:%d" % (where, line), action=action)


def _check(tree, where, allowed_imports):
    out = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in allowed_imports:
                    out.append(_sig("import of %r is not allowed" % a.name, where, line,
                                    "import only %s" % " and ".join(sorted(allowed_imports))))
        elif isinstance(node, ast.ImportFrom):
            if node.level or (node.module or "").split(".")[0] not in allowed_imports:
                out.append(_sig("import from %r is not allowed" % (node.module or "."), where, line,
                                "import only %s" % " and ".join(sorted(allowed_imports))))
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            out.append(_sig("use of %r is not allowed" % node.id, where, line,
                            "use only SDK calls; files, eval and FreeCAD modules are off limits"))
        elif isinstance(node, ast.Attribute) and (node.attr.startswith("_") or node.attr in BANNED_ATTRS):
            out.append(_sig("attribute %r is not part of the SDK surface" % node.attr, where, line,
                            "use the documented SDK calls (docs/sdk.md); raw FreeCAD objects are off limits"))
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            out.append(_sig("global/nonlocal is not allowed", where, line, "pass values as arguments"))
    return out


def _parse(source, where):
    try:
        return ast.parse(source, filename=where), []
    except SyntaxError as e:
        return None, [_sig("syntax error: %s" % e.msg, where, e.lineno or 0, "fix the syntax")]


def lint_program(source: str, where: str = "part.py") -> list[Signal]:
    """L0 for a part program: SDK-only imports and calls, and a top-level build(P)."""
    tree, errs = _parse(source, where)
    if tree is None:
        return errs
    out = _check(tree, where, PROGRAM_IMPORTS)
    builds = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "build"]
    if not builds:
        out.append(_sig("no top-level `def build(P)`", where, 1, "define build(P) returning the Part"))
    elif len(builds[0].args.args) != 1:
        out.append(_sig("build must take exactly one argument (P, the plan params)", where, builds[0].lineno,
                        "write `def build(P):`"))
    return out


def lint_probe(source: str, where: str = "probe") -> list[Signal]:
    """L0 for a probe_part snippet: `math` only; measurement through the `probe` object."""
    tree, errs = _parse(source, where)
    return errs if tree is None else _check(tree, where, PROBE_IMPORTS)
