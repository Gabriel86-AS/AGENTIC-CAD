"""Joint-space samples for the sweep gates (L5 interference, L6 torque, reach). Pure Python 3.11.

Only independent moving joints are sampled; coupled joints (mimic) follow through Tree.joint_values.
Grid = `per_joint` evenly spaced values over each joint's limits (ends included), reduced until the
grid has at most `max_poses` poses (P1 §4.4: 9 per joint, 2 joints -> 81, decimated to 25 -> 5 x 5).
Pose 0 is always the rest pose (all zero, or the nearest limit when 0 is outside them).

sweep_poses (L5, P2 §3.2): the full grid while it fits `max_poses` at 9 per joint down to 5 per joint
(3 joints -> 5^3 = 125); beyond that a Latin hypercube: rest + each joint alone at its two limits + stratified
samples (one per stratum and joint, stratum centres, seeded), `max_poses` in total.
"""
import itertools
import math
import random

from assembly.kinematics import LINEAR, ROTARY


def sampled_joints(plan):
    """[(name, lower, upper)] of the independent moving joints, in plan order."""
    out = []
    for j in plan.get("joints", []):
        if j.get("mimic") or j["type"] not in ROTARY | LINEAR:
            continue
        lim = j.get("limits")
        if lim:
            out.append((j["name"], lim["lower"], lim["upper"]))
        elif j["type"] in ROTARY:          # continuous without limits: a full turn
            out.append((j["name"], -math.pi, math.pi))
    return out


def linspace(a, b, n):
    return [a] if n <= 1 or a == b else [a + (b - a) * i / (n - 1) for i in range(n)]


def rest_pose(plan):
    return {n: min(max(0.0, lo), hi) for n, lo, hi in sampled_joints(plan)}


def grid(plan, per_joint=9, max_poses=25):
    """[rest pose] + grid poses ({joint: q}); duplicates of the rest pose are dropped."""
    joints = sampled_joints(plan)
    rest = rest_pose(plan)
    if not joints:
        return [rest]
    k = per_joint
    while k > 2 and k ** len(joints) > max_poses:
        k -= 1
    axes = [linspace(lo, hi, k) for _, lo, hi in joints]
    poses = [rest]
    for combo in itertools.product(*axes):
        p = {n: q for (n, _, _), q in zip(joints, combo)}
        if p != rest:
            poses.append(p)
    return poses


LHS_MIN_PER_JOINT = 5      # below this a grid misses too much of each joint's range -> Latin hypercube
LHS_SEED = "agenticcad-l5"


def latin_hypercube(plan, n, seed=LHS_SEED):
    """n poses, each joint's range cut into n strata, every stratum used once per joint (stratum centres)."""
    joints = sampled_joints(plan)
    rng = random.Random(seed)
    cols = []
    for _, lo, hi in joints:
        idx = list(range(n))
        rng.shuffle(idx)
        cols.append([lo + (hi - lo) * (i + 0.5) / n for i in idx])
    return [{name: col[k] for (name, _, _), col in zip(joints, cols)} for k in range(n)]


def sweep_poses(plan, per_joint=9, max_poses=125, seed=LHS_SEED):
    """Interference sweep poses: [rest] + grid, or a Latin hypercube when the grid would need < 5 per joint."""
    joints = sampled_joints(plan)
    if len(joints) <= 1 or LHS_MIN_PER_JOINT ** len(joints) <= max_poses:
        return grid(plan, per_joint, max_poses)
    rest = rest_pose(plan)
    poses = [rest]
    for name, lo, hi in joints:
        for q in (lo, hi):
            p = dict(rest, **{name: q})
            if p not in poses:
                poses.append(p)
    return poses + latin_hypercube(plan, max(0, max_poses - len(poses)), seed)


def joint_sweep(plan, joint, samples=9):
    """`samples` values over one joint's limits, every other joint at rest (run_tests expect_clearance)."""
    rest = rest_pose(plan)
    lim = next(((lo, hi) for n, lo, hi in sampled_joints(plan) if n == joint), None)
    if lim is None:
        return [rest]
    return [dict(rest, **{joint: q}) for q in linspace(lim[0], lim[1], samples)]
