"""Plan validator (P1 §2 plan loop): AssemblyPlan -> signals for the design agent, before any part exists.

Checks: cards exist; kinematic tree (one parent per owner, rooted at the ground, connector refs); DOF
(independent moving joints = plan.dof = spec.dof); drivers (actuator cards, one joint each, limits
within the servo range); exactly one `tool` connector for actuated designs; reach (FK sweep with the
declared connector frames); L6-plan static torque with estimated masses (links as rods of
ROD_G_PER_MM along their connector span, card masses at their envelope centres, payload at tool).

Frames come from the plan (L3 holds built parts to them within 0.01 mm) and the cards.
Signals: gate "plan" for structure, "L5" for reach, "L6" for torque; all routed to DESIGN.
"""
import math
from dataclasses import dataclass, field

from assembly.kinematics import ROTARY, Tree, frame, mul, origin, residual
from components.card import connectors as card_connectors
from components.card import load_card
from schemas.plan import MOVING, AssemblyPlan
from schemas.signal import Signal
from validate.masses import actuator_limit, card_com_mm, card_mass_kg
from validate.poses import grid, sampled_joints
from validate.statics import joint_loads, subtree

ROD_G_PER_MM = 0.5            # P1 §4.4 L6-plan default linear density (estimate)
TOOL_LABEL = "tool"
REACH_SAMPLES = (19, 4000)    # per joint, max poses: dense, cheap pure-Python sweeps
WARN_FRACTION = 0.85          # torque above this share of the allowed value -> warning


@dataclass
class PlanReport:
    ok: bool
    signals: list[Signal]
    dof: int = 0
    tool: str | None = None
    reach_mm: float | None = None
    torques: dict = field(default_factory=dict)   # joint -> {max_abs_Nm, allowed_Nm, q}


def _sig(level, gate, cause, location="", action="", **evidence):
    return Signal(level=level, gate=gate, cause=cause, location=location, action=action, evidence=evidence)


def card_frames(card):
    """(connector frames, mount connector name or None) of a card."""
    fr = {n: frame(c["o"], c["z"], c["x"]) for n, c in card_connectors(card).items()}
    mount = next((n for n, t in fr.items() if max(residual(t, frame((0, 0, 0), (0, 0, 1), (1, 0, 0)))) < 1e-9), None)
    return fr, mount


def plan_frames(plan: AssemblyPlan, cards: dict):
    frames = {p.name: {c.name: frame(c.o, c.z, c.x) for c in p.connectors} for p in plan.parts}
    mounts = {}
    for c in plan.components:
        if c.card in cards:
            frames[c.id], m = card_frames(cards[c.card])
            if m:
                mounts[c.id] = m
    return frames, mounts


def tool_connectors(plan: AssemblyPlan):
    return ["%s.%s" % (p.name, c.name) for p in plan.parts for c in p.connectors if c.label == TOOL_LABEL]


def root_joint(tree):
    """The moving joint closest to the ground (first in tree order)."""
    return next((e for e in tree.edges if e.kind == "joint" and e.jtype in MOVING), None)


def reach_sweep(tree, tool, poses):
    """(max horizontal distance tool -> root joint origin in mm, its pose) over `poses`."""
    root = root_joint(tree)
    owner, conn = tool.split(".")
    best, best_q = 0.0, {}
    for pose in poses:
        values, sigs = tree.joint_values(pose)
        if any(s["level"] == "failure" for s in sigs):
            continue
        world = tree.fk(values)
        t = origin(mul(world[owner], tree.frames[owner][conn]))
        p = origin(mul(world[root.parent], tree.frames[root.parent][root.pconn])) if root else [0.0, 0.0, 0.0]
        d = math.hypot(t[0] - p[0], t[1] - p[1])
        if d > best:
            best, best_q = d, values
    return best, best_q


def torque_sweep(tree, plan: AssemblyPlan, cards: dict, masses, poses):
    """Max |gravity load| per driven rotary joint over `poses` vs the servo limit.
    -> ({joint: {max_abs_Nm, allowed_Nm, stall_Nm, derating, q}}, signals)."""
    comps = {c.id: c for c in plan.components}
    below = subtree(tree)
    worst = {}
    for pose in poses:
        values, sigs = tree.joint_values(pose)
        if any(s["level"] == "failure" for s in sigs):
            continue
        for name, load in joint_loads(tree, values, masses, below).items():
            if abs(load) > worst.get(name, (-1.0, None))[0]:
                worst[name] = (abs(load), values)
    out, sigs = {}, []
    for j in plan.joints:
        if j.name not in worst or not j.driver or j.driver not in comps or j.type not in ROTARY:
            continue
        lim = actuator_limit(cards.get(comps[j.driver].card, {}), j.servos_on_axis)
        if lim is None:
            continue
        allowed, stall, derating = lim
        tau, q = worst[j.name]
        out[j.name] = {"max_abs_Nm": tau, "allowed_Nm": allowed, "stall_Nm": stall, "derating": derating, "q": q}
        ev = dict(max_abs_Nm=round(tau, 4), allowed_Nm=round(allowed, 4), q={k: round(v, 4) for k, v in q.items()},
                  payload_g=plan.requirements.payload_g or 0)
        where = "plan.joints.%s" % j.name
        if tau > allowed:
            sigs.append(_sig("failure", "L6", "static torque %.3f N.m at %r > allowed %.3f N.m (%s stall %.2f N.m x %.2g x %d)"
                             % (tau, j.name, allowed, comps[j.driver].card, stall, derating, j.servos_on_axis), where,
                             "shorten the links beyond %r, lighten them, move servos toward the base, add a second servo "
                             "on the axis (servos_on_axis) or choose a stronger servo" % j.name, **ev))
        elif tau > WARN_FRACTION * allowed:
            sigs.append(_sig("warning", "L6", "static torque %.3f N.m at %r is %d %% of the allowed %.3f N.m"
                             % (tau, j.name, round(100 * tau / allowed), allowed), where,
                             "little margin left for printed-mass growth", **ev))
    return out, sigs


def rod_masses(plan: AssemblyPlan, cards: dict, linear_g_per_mm=ROD_G_PER_MM):
    """[(owner, kg, com_mm)]: parts as rods over their connector span, cards at their envelope centre."""
    out = []
    for p in plan.parts:
        pts = [c.o for c in p.connectors]
        if not pts:
            continue
        span = max((math.dist(a, b) for a in pts for b in pts), default=0.0)
        com = [sum(v[i] for v in pts) / len(pts) for i in range(3)]
        out.append((p.name, span * linear_g_per_mm / 1000.0, com))
    for c in plan.components:
        if c.card in cards:
            out.append((c.id, card_mass_kg(cards[c.card]), card_com_mm(cards[c.card])))
    return out


def payload_mass(plan: AssemblyPlan, tool):
    if not tool or not plan.requirements.payload_g:
        return []
    owner, conn = tool.split(".")
    c = next(c for c in plan.part(owner).connectors if c.name == conn)
    return [(owner, plan.requirements.payload_g / 1000.0, list(c.o))]


def test_ref_signals(plan: AssemblyPlan, frames: dict) -> list[Signal]:
    """plan.tests and plan.allow_overlap name existing owners, connectors and sampled joints."""
    sigs = []
    owners = set(frames)
    sampled = {n for n, _, _ in sampled_joints(plan.model_dump())}
    pairs = [("plan.allow_overlap[%d]" % i, a, b) for i, (a, b) in enumerate(plan.allow_overlap)]
    pairs += [("plan.tests[%d]" % i, t.a, t.b) for i, t in enumerate(plan.tests) if hasattr(t, "a")]
    for where, a, b in pairs:
        bad = [o for o in (a, b) if o not in owners]
        if bad:
            sigs.append(_sig("failure", "plan", "%s names unknown owner %s" % (where, ", ".join(map(repr, bad))), where,
                             "use part names or component ids: %s" % ", ".join(sorted(owners))))
        elif a == b:
            sigs.append(_sig("failure", "plan", "%s pairs %r with itself" % (where, a), where, "name two different owners"))
    for i, t in enumerate(plan.tests):
        where = "plan.tests[%d]" % i
        ref = getattr(t, "near", None) or getattr(t, "tool", None)
        if ref:
            o, c = ref.split(".")
            if o not in frames or c not in frames[o]:
                sigs.append(_sig("failure", "plan", "%s: no connector %r" % (where, ref), where,
                                 "reference an existing '<owner>.<connector>'"))
        if getattr(t, "over_joint", None) and t.over_joint not in sampled:
            sigs.append(_sig("failure", "plan", "%s: over_joint %r is not an independent moving joint" % (where, t.over_joint),
                             where, "pick one of: %s" % ", ".join(sorted(sampled))))
    return sigs


def check_plan(plan: AssemblyPlan) -> PlanReport:
    sigs: list[Signal] = []
    cards = {}
    for c in plan.components:
        try:
            cards[c.card] = load_card(c.card)
        except FileNotFoundError:
            sigs.append(_sig("failure", "plan", "component %r: no card %r in the library" % (c.id, c.card),
                             "plan.components.%s" % c.id, "use a card id from components/ (e.g. servo/mg996r)"))
    frames, mounts = plan_frames(plan, cards)
    for c in plan.components:
        if c.card in cards and c.id not in mounts:
            sigs.append(_sig("failure", "plan", "card %s has no mount connector at its frame origin" % c.card,
                             "components/%s.yaml" % c.card))
    tree = Tree(plan.model_dump(), frames, mounts)
    sigs += [_sig(s["level"], "plan", s["cause"], s["location"], s["action"]) for s in tree.signals]

    # DOF
    counted = sum(1 for j in plan.joints if j.type in MOVING and j.mimic is None)
    if counted != plan.dof:
        sigs.append(_sig("failure", "plan", "plan.dof = %d but the plan has %d independent moving joints" % (plan.dof, counted),
                         "plan.dof", "set dof to the number of independent revolute/continuous/slider/cylindrical joints"))
    if plan.dof != plan.requirements.dof:
        sigs.append(_sig("failure", "plan", "plan.dof = %d, the spec asks for %d" % (plan.dof, plan.requirements.dof),
                         "plan.dof", "add or remove joints to match the spec, or confirm a spec change with the user"))

    # drivers
    comps = {c.id: c for c in plan.components}
    used = {}
    for j in plan.joints:
        where = "plan.joints.%s" % j.name
        if j.driver is None:
            if j.type in MOVING and j.mimic is None:
                sigs.append(_sig("note", "plan", "joint %r is passive (no driver)" % j.name, where))
            continue
        if j.driver not in comps:
            sigs.append(_sig("failure", "plan", "joint %r: driver %r is not a component" % (j.name, j.driver), where,
                             "add the actuator to plan.components or fix the id"))
            continue
        card = cards.get(comps[j.driver].card)
        if card is None:
            continue
        if not str(card.get("kind", "")).startswith("actuator"):
            sigs.append(_sig("failure", "plan", "joint %r: driver %r is a %s, not an actuator" % (j.name, j.driver, card.get("kind")),
                             where, "drive the joint with a servo card"))
            continue
        if j.driver in used:
            sigs.append(_sig("failure", "plan", "servo %r drives both %r and %r" % (j.driver, used[j.driver], j.name), where,
                             "one servo per joint; couple joints with mimic instead"))
        used[j.driver] = j.name
        if j.driver not in (j.parent.split(".")[0], j.child.split(".")[0]):
            sigs.append(_sig("warning", "plan", "joint %r is driven by %r but neither side is on that servo" % (j.name, j.driver),
                             where, "mate the joint on the servo's horn connector, e.g. parent %s.horn" % j.driver))
        rng = (card.get("actuation") or {}).get("range_deg")
        if rng and j.limits and j.type in ROTARY:
            lo, hi = math.radians(rng[0]), math.radians(rng[1])
            if j.limits.lower < lo - 1e-6 or j.limits.upper > hi + 1e-6:
                sigs.append(_sig("failure", "plan", "joint %r limits [%.3g, %.3g] rad exceed %s travel [%.3g, %.3g] rad"
                                 % (j.name, j.limits.lower, j.limits.upper, comps[j.driver].card, lo, hi), where,
                                 "clamp the limits to the servo range", servo_range_deg=rng))

    sigs += test_ref_signals(plan, frames)

    # tool
    tools = tool_connectors(plan)
    actuated = any(j.driver for j in plan.joints)
    tool = tools[0] if len(tools) == 1 else None
    if len(tools) > 1:
        sigs.append(_sig("failure", "plan", "%d connectors are labelled %r: %s" % (len(tools), TOOL_LABEL, tools),
                         "plan.parts", "label exactly one connector 'tool' (it becomes tool0)"))
    elif not tools and (actuated or plan.requirements.reach_mm or plan.requirements.payload_g):
        sigs.append(_sig("failure", "plan", "no connector is labelled %r" % TOOL_LABEL, "plan.parts",
                         "declare the end-effector frame on the last link with label 'tool' (it becomes tool0)"))

    report = PlanReport(False, sigs, counted, tool)
    if tree.ok and not any(s.level == "failure" for s in sigs):
        poses = grid(plan.model_dump(), *REACH_SAMPLES)
        want = plan.requirements.reach_mm
        if tool:
            report.reach_mm, q = reach_sweep(tree, tool, poses)
            if want and report.reach_mm < want - 1e-6:
                sigs.append(_sig("failure", "L5", "reach %.1f mm < spec %.1f mm (tool %s)" % (report.reach_mm, want, tool),
                                 "plan.params", "lengthen the links between the first moving joint and the tool by "
                                 "about %.0f mm" % (want - report.reach_mm), reach_mm=round(report.reach_mm, 2),
                                 spec_mm=want, q={k: round(v, 4) for k, v in q.items()}))
        masses = rod_masses(plan, cards) + payload_mass(plan, tool)
        report.torques, tsigs = torque_sweep(tree, plan, cards, masses, poses)
        for s in tsigs:
            s.cause = "(plan estimate, rods %.2g g/mm) " % ROD_G_PER_MM + s.cause
        sigs += tsigs
    report.ok = not any(s.level == "failure" for s in sigs)
    return report
