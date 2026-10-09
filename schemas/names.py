"""Name rules shared by the orchestrator (pydantic schemas) and the SDK inside freecadcmd.

Pure Python 3.11, no third-party imports: the SDK imports this module too.
"""
import re

# FreeCAD's expression parser reads these as units, so Spreadsheet.setAlias rejects them (S7 saw
# h, m, mm, s, g, deg fail). The list is not exhaustive: the SDK still reports a setAlias failure.
UNIT_SYMBOLS = frozenset("""
    nm um mm cm dm m km in inch ft foot feet yd yard mi mile thou mil
    s ms min h hr hour
    ug mg g kg t lb oz st
    deg rad gon mrad
    n kn mn pa kpa mpa gpa psi ksi bar mbar torr
    w kw mw j kj v mv kv a ma k mk ohm f c l ml
""".split())

NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def name_error(name: str) -> str | None:
    """None if `name` is a valid param alias / part / connector / joint name, else the reason."""
    if not isinstance(name, str) or not NAME_RE.match(name):
        return "%r: use lower_snake_case starting with a letter" % (name,)
    if name.lower() in UNIT_SYMBOLS:
        return "%r collides with a FreeCAD unit symbol; use a descriptive name (e.g. h -> height)" % (name,)
    return None


def split_ref(ref: str) -> tuple[str, str]:
    """'owner.connector' -> ('owner', 'connector'); raises ValueError otherwise."""
    owner, sep, conn = ref.partition(".")
    if not sep or not owner or not conn or "." in conn:
        raise ValueError("%r: expected '<owner>.<connector>'" % (ref,))
    return owner, conn
