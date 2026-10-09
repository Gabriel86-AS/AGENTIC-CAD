"""Joint-space samples for the sweep gates (L5 interference, L6 torque, reach). Pure Python 3.11.

Only independent moving joints are sampled; coupled joints (mimic) follow through Tree.joint_values.
Grid = `per_joint` evenly spaced values over each joint's limits (ends included), reduced until the
grid has at most `max_poses` poses (P1 §4.4: 9 per joint, 2 joints -> 81, decimated to 25 -> 5 x 5).
Pose 0 is always the rest pose (all zero, or the nearest limit when 0 is outside them).
"""
import itertools
import math

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
