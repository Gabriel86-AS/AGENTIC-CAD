"""What each agent gets to see (plan §3.4): the card library index, a part's plan excerpt, the checkpoint.

The part excerpt is the whole contract a part agent works to: its connectors (exact poses), the plan params
it may read as P.<name>, the components seated on its connectors, the joints it takes part in and its
neighbours' intents. Part agents never see other parts' programs (ArtiCAD Eq. 3: independent given the plan).
"""
import json
import math

from components.card import card_ids, load_card
from schemas.plan import AssemblyPlan
from validate.plan_check import PlanReport

AMPS_PER_SERVO_STALL = "stall_current_A"


def library_index() -> str:
    lines = []
    for cid in card_ids():
        c = load_card(cid)
        first = " ".join(str(c.get("source", "")).split())[:110]
        lines.append("- `%s` (%s, %s): %s" % (cid, c.get("kind"), "verified" if c.get("verified") else "unverified", first))
    return "\n".join(lines)


def _owner(ref: str) -> tuple[str, str]:
    owner, _, conn = ref.partition(".")
    return owner, conn


def _deg(v: float) -> str:
    return "%.1f°" % math.degrees(v)


def part_excerpt(plan: AssemblyPlan, name: str) -> str:
    spec = plan.part(name)
    comps = {c.id: c for c in plan.components}
    out = ["# Part `%s` — your contract" % name, "",
           "**Intent:** %s" % spec.intent, "**Material:** %s" % spec.material]
    if spec.bbox_mm:
        out.append("**Intended size (bbox, ±20 %% checked):** %s mm" % list(spec.bbox_mm))
    out += ["", "## Connectors you must create (exact pose and label, part frame, mm)"]
    for c in spec.connectors:
        out.append("- `%s`: `part.connector(%r, origin=%s, z=%s, x=%s, label=%r)`"
                   % (c.name, c.name, list(c.o), list(c.z), list(c.x), c.label))
    out += ["", "Build the origins from params (part params or `P.<name>`) whose values give exactly these numbers."]
    if plan.params:
        out += ["", "## Plan params (`P.<name>`; the user edits these)"]
        for k, p in plan.params.items():
            out.append("- `P.%s` = %g %s (range %g .. %g)" % (k, p.value, p.unit, p.min, p.max))
    seated = [c for c in plan.components if _owner(c.attach_to)[0] == name]
    if seated:
        out += ["", "## Components seated on your connectors"]
        for c in seated:
            kind = load_card(c.card).get("kind", "")
            call = ("part.electronics_bay(%r, at=%r)" if kind.startswith("electronics")
                    else "part.bearing_seat(%r, at=%r)" if kind.startswith("bearing")
                    else "part.mount(%r, at=%r)") % (c.id, _owner(c.attach_to)[1])
            out.append("- `%s` = card `%s` (%s) at connector `%s` -> `%s`" % (c.id, c.card, kind, _owner(c.attach_to)[1], call))
    joints = [j for j in plan.joints if name in (_owner(j.parent)[0], _owner(j.child)[0])]
    if joints:
        out += ["", "## Joints you take part in"]
        for j in joints:
            lim = " %s .. %s" % (_deg(j.limits.lower), _deg(j.limits.upper)) if j.limits and j.type != "slider" else \
                (" %g .. %g mm" % (j.limits.lower, j.limits.upper) if j.limits else "")
            line = "- `%s` (%s%s): parent `%s` -> child `%s`" % (j.name, j.type, lim, j.parent, j.child)
            p_owner, p_conn = _owner(j.parent)
            if _owner(j.child)[0] == name and p_owner in comps and p_conn == "horn":
                srv = comps[p_owner]
                line += ("\n  You are driven by servo `%s`'s horn: cut its interface with "
                         "`part.mate_to(\"%s.horn\", at=%r)`, give a thin link a hub with `part.horn_coupler(%r, at=%r)`, "
                         "or wrap it with `part.servo_u_bracket(%r, at=%r, ...)`."
                         % (p_owner, p_owner, _owner(j.child)[1], p_owner, _owner(j.child)[1], p_owner, _owner(j.child)[1]))
                host = _owner(srv.attach_to)[0]
                line += " The servo body is fixed to `%s`; stay clear of it across the joint range." % host
            out.append(line)
    tests = [t for t in plan.tests if t.expect in ("contact", "clearance") and name in (t.a, t.b)]
    if tests:
        out += ["", "## Assembly tests on your part (checked once every part compiles)"]
        for t in tests:
            other = t.b if t.a == name else t.a
            if t.expect == "contact":
                out.append("- touch `%s` at rest (gap <= %g mm)%s" % (other, t.tol_mm, " near `%s`" % t.near if t.near else ""))
            else:
                over = "over `%s`" % t.over_joint if t.over_joint else "over every joint"
                out.append("- stay >= %g mm from `%s` %s" % (t.min_mm, other, over))
    others = [p for p in plan.parts if p.name != name]
    if others:
        out += ["", "## Neighbours (other agents build these; do not model them)"]
        out += ["- `%s`: %s" % (p.name, p.intent) for p in others]
    return "\n".join(out) + "\n"


def checkpoint_text(plan: AssemblyPlan, report: PlanReport, bom: dict) -> str:
    out = ["== CHECKPOINT ==", "", "Components:"]
    for b in bom["items"]:
        used = [c.id for c in plan.components if c.card == b["card"]]
        out.append("  %-26s x%d  %-10s %s" % (b["card"], len(used), "verified" if b["verified"] else "UNVERIFIED",
                                             ", ".join(used)))
    out += ["", "Joints:"]
    for j in plan.joints:
        lim = ""
        if j.limits:
            lim = ("%s to %s" % (_deg(j.limits.lower), _deg(j.limits.upper))) if j.type != "slider" else \
                "%g to %g mm" % (j.limits.lower, j.limits.upper)
        comps = {c.id: c for c in plan.components}
        parent = _owner(j.parent)[0]
        host = _owner(comps[parent].attach_to)[0] if parent in comps else parent
        who = ("%s drives %s (servo on %s)" % (j.driver, _owner(j.child)[0], host)) if j.driver else             ("%s on %s, passive" % (_owner(j.child)[0], host))
        out.append("  %s: %s, %s %s" % (j.name, who, j.type, lim))
    out += ["", "Estimates (plan level: links as rods, card masses):"]
    if report.reach_mm is not None:
        want = plan.requirements.reach_mm
        out.append("  reach %.0f mm%s" % (report.reach_mm, " (spec %.0f)" % want if want else ""))
    for j, t in report.torques.items():
        out.append("  %s static torque %.2f N.m of %.2f allowed (%.0f %%)"
                   % (j, t["max_abs_Nm"], t["allowed_Nm"], 100 * t["max_abs_Nm"] / t["allowed_Nm"] if t["allowed_Nm"] else 0))
    servos = [c for c in plan.components if load_card(c.card).get("kind", "").startswith("actuator")]
    if servos:
        amps = sum(load_card(c.card).get("electrical", {}).get(AMPS_PER_SERVO_STALL, 0) for c in servos)
        out.append("  power: %d servos, ~%.1f A combined stall; use an external 5-6 V supply" % (len(servos), amps))
    out += ["", "Print: profile %s, material %s" % (plan.requirements.print_profile, plan.requirements.material),
            "Parts: " + ", ".join(p.name for p in plan.parts), ""]
    return "\n".join(out)


def plan_summary_json(plan: AssemblyPlan) -> str:
    return json.dumps(plan.model_dump(), indent=1)
