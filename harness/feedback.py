"""Signal formatter (P1 T1.5, plan §4.3): turn gate signals into short, actionable agent feedback.

Articraft-style failure / warning / note lines with ECIP cause / location / action, failures first,
duplicates merged, long fields clipped, so a part agent gets a fixed-size answer instead of a log.
"""
from schemas.signal import Signal

ORDER = {"failure": 0, "warning": 1, "note": 2}
MAX_FIELD = 300


def _clip(s: str, n: int = MAX_FIELD) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 3] + "..."


def sort_signals(signals: list[Signal]) -> list[Signal]:
    """Failures, then warnings, then notes (stable within a level); exact duplicates dropped."""
    seen, out = set(), []
    for s in sorted(signals, key=lambda s: ORDER[s.level]):
        key = (s.level, s.gate, s.cause, s.location)
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def format_signal(s: Signal) -> str:
    line = "- %s %s: %s" % (s.level.upper(), s.gate, _clip(s.cause))
    if s.location:
        line += " | at %s" % _clip(s.location, 160)
    if s.action:
        line += " | fix: %s" % _clip(s.action)
    return line


def format_feedback(tool: str, signals: list[Signal], summary: str = "", max_signals: int = 8) -> str:
    """One status line, then up to max_signals signal lines (the rest counted, not shown)."""
    sigs = sort_signals(signals)
    n = {lvl: sum(s.level == lvl for s in sigs) for lvl in ORDER}
    counts = ", ".join("%d %s%s" % (n[lvl], lvl, "s" if n[lvl] != 1 else "") for lvl in ORDER if n[lvl])
    status = "FAIL" if n["failure"] else "PASS"
    head = "%s: %s" % (tool, status) + (" (%s)" % counts if counts else "") + (" - %s" % summary if summary else "")
    lines = [head] + [format_signal(s) for s in sigs[:max_signals]]
    if len(sigs) > max_signals:
        lines.append("(+%d more signals not shown; fix the ones above first)" % (len(sigs) - max_signals))
    return "\n".join(lines)
