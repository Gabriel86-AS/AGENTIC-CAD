"""Description checks D1, D2, D4 (plan §5.4) on an exported package. Linux tools run in Docker (D24).

  D1  check_urdf (urdfdom) + yourdfpy load with every package:// mesh resolved, one root
  D2  Pinocchio: every body mass > 0, inertia positive-definite and triangle inequality; joint-space
      inertia matrix (CRBA) PD at sampled q; generalized gravity (RNEA, v = a = 0) equals
      -validate.statics.joint_loads at the same q (cross-check of the L6 gate, D33)
  D4  `gz sdf -p` converts the URDF without errors

Runner: env AGENTICCAD_LINUX = "docker" (default; image AGENTICCAD_ROS_IMAGE, default
agenticcad-ros:jazzy) or "wsl" (AGENTICCAD_WSL_DISTRO, default Ubuntu-22.04). The package dir is mounted
read-only at /pkg. A tool that cannot run is a failure signal, never a pass.
"""
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yourdfpy

from schemas.signal import Signal

RUNNER = os.environ.get("AGENTICCAD_LINUX", "docker")
IMAGE = os.environ.get("AGENTICCAD_ROS_IMAGE", "agenticcad-ros:jazzy")
DISTRO = os.environ.get("AGENTICCAD_WSL_DISTRO", "Ubuntu-22.04")
ROS_SETUP = "/opt/ros/jazzy/setup.bash" if RUNNER == "docker" else "/opt/ros/humble/setup.bash"
GRAVITY_TOL_NM = 1e-6


@dataclass
class DescriptionCheck:
    ok: bool
    signals: list[Signal]
    results: dict = field(default_factory=dict)


def _sig(gate, cause, action="", level="failure", **evidence):
    return Signal(level=level, gate=gate, cause=cause, location="urdf", action=action, evidence=evidence)


def _lpath(pkg: Path, p: Path) -> str:
    if RUNNER == "docker":
        return "/pkg/" + p.relative_to(pkg).as_posix()
    drive, rest = str(p).split(":", 1)
    return f"/mnt/{drive.lower()}{rest.replace(chr(92), '/')}"


def linux(pkg: Path, cmd: str, stdin: str | None = None, timeout=240) -> subprocess.CompletedProcess:
    script = f"source {ROS_SETUP} 2>/dev/null; {cmd}"
    if RUNNER == "docker":
        argv = ["docker", "run", "--rm", "-i", "-v", f"{pkg}:/pkg:ro", IMAGE, "bash", "-c", script]
    else:
        argv = ["wsl", "-d", DISTRO, "-e", "bash", "-c", script]
    return subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")


def d1(pkg: Path, urdf: Path):
    sigs, out = [], {}
    r = linux(pkg, f"check_urdf '{_lpath(pkg, urdf)}'")
    out["check_urdf"] = r.stdout.strip().splitlines()[-8:]
    if r.returncode != 0 or "Successfully Parsed XML" not in r.stdout:
        sigs.append(_sig("D1", "check_urdf failed: %s" % (r.stderr.strip()[-400:] or r.stdout.strip()[-400:]),
                         "exporter bug: inspect the URDF"))
    name = pkg.name

    def handler(fname):
        return str(pkg / fname.replace("package://%s/" % name, "")) if fname.startswith("package://") else fname

    try:
        robot = yourdfpy.URDF.load(str(urdf), filename_handler=handler, load_meshes=True, build_collision_scene_graph=True)
        n_mesh = sum(1 for link in robot.robot.links for v in link.visuals + link.collisions
                     if v.geometry.mesh is not None)
        missing = [f for link in robot.robot.links for v in link.visuals + link.collisions if v.geometry.mesh is not None
                   for f in [handler(v.geometry.mesh.filename)] if not Path(f).exists()]
        roots = [l.name for l in robot.robot.links if l.name not in {j.child for j in robot.robot.joints}]
        out["yourdfpy"] = {"links": len(robot.robot.links), "joints": len(robot.robot.joints), "meshes": n_mesh,
                           "actuated": robot.actuated_joint_names, "roots": roots}
        if missing:
            sigs.append(_sig("D1", "mesh files missing: %s" % missing, "exporter bug"))
        if len(roots) != 1:
            sigs.append(_sig("D1", "URDF has %d roots: %s" % (len(roots), roots), "exporter bug"))
    except Exception as e:
        sigs.append(_sig("D1", "yourdfpy failed to load: %s: %s" % (type(e).__name__, e), "exporter bug"))
    return out, sigs


D2_SCRIPT = r"""
import json, sys
import numpy as np, pinocchio as pin
args = json.loads(sys.stdin.read())
m = pin.buildModelFromUrdf(args["urdf"]); d = m.createData()
bodies = []
for i in range(1, m.njoints):
    I = m.inertias[i]
    ev = np.linalg.eigvalsh(I.inertia).tolist()
    bodies.append({"joint": m.names[i], "mass": I.mass, "eig": ev})
poses = []
for q in args["poses"]:
    v = pin.neutral(m)
    for name, val in q.items():
        if not m.existJointName(name):
            continue
        j = m.joints[m.getJointId(name)]
        if j.nq == 2:   # continuous: (cos, sin)
            v[j.idx_q], v[j.idx_q + 1] = np.cos(val), np.sin(val)
        else:
            v[j.idx_q] = val
    M = pin.crba(m, d, v); M = np.triu(M) + np.triu(M, 1).T
    g = pin.computeGeneralizedGravity(m, d, v)
    poses.append({"q": q, "crba_min_eig": float(np.linalg.eigvalsh(M).min()),
                  "gravity": {m.names[i]: float(g[m.joints[i].idx_v]) for i in range(1, m.njoints)}})
print(json.dumps({"nq": m.nq, "nv": m.nv, "bodies": bodies, "poses": poses}))
"""


def d2(pkg: Path, urdf: Path, poses: list[dict], expected: list[dict] | None):
    """`expected`: per pose {joint: load} from validate.statics (pinocchio gravity should be -load)."""
    sigs = []
    r = linux(pkg, "python3 -c " + _shq(D2_SCRIPT), stdin=json.dumps({"urdf": _lpath(pkg, urdf), "poses": poses}))
    if r.returncode != 0:
        return {}, [_sig("D2", "pinocchio failed: %s" % r.stderr.strip()[-800:], "exporter bug or broken inertials")]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    for b in out["bodies"]:
        e = sorted(b["eig"])
        if b["mass"] <= 0:
            sigs.append(_sig("D2", "body after joint %r has mass %g" % (b["joint"], b["mass"]), "check link masses"))
        elif e[0] <= 0 or e[0] + e[1] < e[2] * (1 - 1e-9):
            sigs.append(_sig("D2", "body after joint %r: principal inertia %s not PD / violates the triangle inequality"
                             % (b["joint"], e), "check link inertials", eig=e))
    worst = 0.0
    for i, p in enumerate(out["poses"]):
        if p["crba_min_eig"] <= 0:
            sigs.append(_sig("D2", "joint-space inertia not PD at q = %s" % p["q"], "check link inertials"))
        for j, load in (expected[i] if expected else {}).items():
            if j in p["gravity"]:
                worst = max(worst, abs(p["gravity"][j] + load))
    out["gravity_max_err_Nm"] = worst
    if expected and worst > GRAVITY_TOL_NM:
        sigs.append(_sig("D2", "Pinocchio gravity differs from the L6 statics by %.3g N.m" % worst,
                         "statics / exporter mass mismatch: compare link inertials with the owner masses", err=worst))
    return out, sigs


def _shq(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


def d4(pkg: Path, urdf: Path):
    r = linux(pkg, f"gz sdf -p '{_lpath(pkg, urdf)}'")
    ok = r.returncode == 0 and "<sdf" in r.stdout
    out = {"returncode": r.returncode, "stderr": r.stderr.strip()[-400:]}
    return out, [] if ok else [_sig("D4", "gz sdf -p failed: %s" % out["stderr"], "exporter bug: inspect the URDF")]


def check_description(package: str | Path, urdf: str | Path, poses: list[dict] | None = None,
                      expected_loads: list[dict] | None = None) -> DescriptionCheck:
    pkg, urdf = Path(package).resolve(), Path(urdf).resolve()
    results, sigs = {}, []
    for name, fn in (("D1", lambda: d1(pkg, urdf)), ("D2", lambda: d2(pkg, urdf, poses or [{}], expected_loads)),
                     ("D4", lambda: d4(pkg, urdf))):
        try:
            results[name], s = fn()
        except Exception as e:
            results[name], s = {}, [_sig(name, "%s could not run: %s: %s" % (name, type(e).__name__, e),
                                         "start Docker Desktop (image %s) or set AGENTICCAD_LINUX=wsl" % IMAGE)]
        sigs += s
    return DescriptionCheck(not any(s.level == "failure" for s in sigs), sigs, results)
