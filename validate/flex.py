"""L4 flex test (P2 §3.1), worker side: does a compiled part stay valid over its param ranges?

Params varied: every alias with a declared [min, max] (plan params, part params given as dicts) and every
named part param without one (`part.params(x=5)`, mm, non-zero) over nominal +-IMPLIED (user 2026-10-10).
Auto-promoted literals, card and profile values never flex; a dict with min == max pins a part param.
Each is recomputed at min, max and one interior value from random.Random("<seed>:<alias>"), the others
at nominal.
A variant passes if no object is Invalid / in Error, every sketch solves fully constrained, the body is
one valid solid and every connector placement is finite. At most MAX_RUNS recomputes per part; order:
connector-driving params (directly or through other cells) before the rest, declared before implied;
params past the cap are reported as skipped.

Runs on the in-memory document after the nominal model is saved; nominal values are restored at the end
and the nominal volume re-checked (a sketch that flips at an extreme may not flip back: warning).
"""
import math
import random
import re
import time

from sdk.errors import signal

MAX_RUNS = 30
IMPLIED = 0.2
_ALIAS = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PARAMS_REF = re.compile(r"\bParams\.([A-Za-z_][A-Za-z0-9_]*)")
_LITERAL = re.compile(r"^=\s*[-+]?[0-9.]+(e[-+]?[0-9]+)?\s*[a-z]*$", re.I)


def _contents(part, alias):
    return part.sheet.getContents(part.sheet.getCellFromAlias(alias))


def ranges(part, implied=IMPLIED, sources=("part",)):
    """{alias: (lo, hi)} in sheet order: declared ranges, plus +-implied of the nominal for params without
    one whose source is in `sources` (agents: named part params; SDK tests add "literal")."""
    out = {}
    for a, i in part.params_info.items():
        if i["min"] is not None and i["max"] is not None and i["max"] > i["min"]:
            out[a] = (i["min"], i["max"])
        elif (implied and i["source"] in sources and i["min"] is None and i["value"] != 0 and i["unit"] == "mm"
              and _LITERAL.match(_contents(part, a))):   # expression cells follow their inputs instead
            d = abs(i["value"]) * implied
            out[a] = (i["value"] - d, i["value"] + d)
    return out


def connector_params(part):
    """Aliases the connector placements depend on, through any chain of Params cells."""
    deps = {}
    for alias in part.params_info:
        text = _contents(part, alias)
        deps[alias] = {t for t in _ALIAS.findall(text.lstrip("=")) if t in part.params_info and t != alias}
    todo = [m for c in part.connectors.values() for _, e in c["obj"].ExpressionEngine for m in _PARAMS_REF.findall(e)]
    seen = set()
    while todo:
        a = todo.pop()
        if a in part.params_info and a not in seen:
            seen.add(a)
            todo.extend(deps[a])
    return seen


def plan_runs(part, seed, max_runs=MAX_RUNS, implied=IMPLIED, sources=("part",)):
    """[(alias, which, value)] in test order, the ranged aliases left out by the cap, the ranges."""
    rng = ranges(part, implied, sources)
    hot = connector_params(part)
    names = sorted(rng, key=lambda a: (a not in hot, part.params_info[a]["min"] is None))   # stable: sheet order
    runs, skipped = [], []
    for a in names:
        if len(runs) + 3 > max_runs:
            skipped.append(a)
            continue
        lo, hi = rng[a]
        mid = random.Random("%s:%s" % (seed, a)).uniform(lo, hi)
        runs += [(a, "min", lo), (a, "max", hi), (a, "interior", mid)]
    return runs, skipped, rng


def _broken(part):
    """First thing wrong with the current recompute, as (feature, what), or None."""
    doc = part.doc
    order = {o.Name: k for k, o in enumerate(part.body.Group)}
    bad = [o for o in doc.Objects if "Invalid" in o.State or "Error" in o.State]
    if bad:
        o = min(bad, key=lambda o: order.get(o.Name, len(order)))
        return o.Name, "invalid: %s" % (o.getStatusString() or ",".join(o.State))
    for name, s in part.sketches.items():
        sk = s.obj
        rc = sk.solve()
        if rc != 0 or not sk.FullyConstrained:
            what = "solve=%d DoF=%d" % (rc, sk.DoF)
            if sk.ConflictingConstraints:
                what += " conflicting %s" % list(sk.ConflictingConstraints)
            return name, "sketch " + what
    shape = part.body.Shape
    n = 0 if shape.isNull() else len(shape.Solids)
    if n != 1 or not shape.isValid() or shape.Volume <= 0:
        return "Body", "%d solids, volume %.6g mm3" % (n, 0.0 if shape.isNull() else shape.Volume)
    for name, c in part.connectors.items():
        b = c["obj"].Placement.Base
        if not all(math.isfinite(v) for v in (b.x, b.y, b.z)):
            return name, "connector placement is not finite"
    return None


def flex(part, seed, display, max_runs=MAX_RUNS, implied=IMPLIED, sources=("part",)):
    """Run L4 on a Part that passed L1-L3. Returns (facts dict, signals)."""
    t0 = time.monotonic()
    runs, skipped, rng = plan_runs(part, seed, max_runs, implied, sources)
    nominal = part.body.Shape.Volume
    failures, failed, n = [], set(), 0
    for alias, which, value in runs:
        if alias in failed:        # one signal per param: its first broken variant
            continue
        n += 1
        cell, text = part.sheet.getCellFromAlias(alias), _contents(part, alias)
        part.set_param(alias, value)
        part.doc.recompute()
        hit = _broken(part)
        part.sheet.set(cell, text)
        if hit:
            failed.add(alias)
            failures.append({"param": alias, "which": which, "value": value, "feature": hit[0], "what": hit[1]})
            part.doc.recompute()   # back to nominal before the next variant
    part.doc.recompute()
    restored = _broken(part) is None and abs(part.body.Shape.Volume - nominal) <= 1e-6 * max(1.0, nominal)

    sigs = []
    for f in failures:
        i, (lo, hi) = part.params_info[f["param"]], rng[f["param"]]
        unit = "" if i["unit"] in ("", "-") else " " + i["unit"]
        if i["source"] == "plan":
            scope = "if the plan range itself is impossible, say so (the design owns it)"
        elif i["min"] is None:
            scope = ("%s has no declared range, so it is flexed +-%d %%; if it must not move that far declare one: "
                     "part.params(%s={\"value\": %.6g, \"min\": ..., \"max\": ...})" % (
                         f["param"], round(implied * 100), f["param"], i["value"]))
        else:
            scope = "or narrow the declared range of %s" % f["param"]
        sigs.append(signal(
            "failure", "L4",
            "param %s at %s = %.6g%s (range %.6g..%.6g, nominal %.6g): %s %s" % (
                f["param"], f["which"], f["value"], unit, lo, hi, i["value"], f["feature"], f["what"]),
            "%s param %s -> %s" % (display, f["param"], f["feature"]),
            "make %s valid over the whole range: derive its sizes and positions from %s (and the other params) "
            "instead of fixed numbers, keep clearances >= 0 at both ends; %s" % (f["feature"], f["param"], scope)))
    if not restored:
        sigs.append(signal("warning", "L4", "model did not return to its nominal shape after the flex runs "
                           "(a sketch may flip to another solution at an extreme)", display,
                           "fully constrain orientation in sketches (angles / horizontal / vertical, not only distances)"))
    if skipped:
        sigs.append(signal("info", "L4", "flex cap %d recomputes: not tested %s" % (max_runs, skipped), display))
    facts = {"tested": sorted({a for a, _, _ in runs}), "runs": n, "skipped": skipped, "failures": failures, "restored": restored,
             "elapsed_s": round(time.monotonic() - t0, 3)}
    return facts, sigs
