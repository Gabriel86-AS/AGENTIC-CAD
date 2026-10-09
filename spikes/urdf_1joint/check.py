"""T0.7 checks for the 1-joint URDF package.

    uv run python -m spikes.urdf_1joint.check

  yourdfpy   load (package:// resolved to this dir), FK at q = 0.5 rad vs. origin * Rz(q)  [Windows venv]
  check_urdf urdfdom parser                                                             [WSL, ROS 2 setup]
  gz sdf -p  URDF -> SDF conversion                                                     [WSL, Gazebo]
  pinocchio  buildModel, FK at q = 0.5 rad                                              [WSL python]
A tool that is not installed is reported as "unavailable", never as a pass.
WSL distro / ROS setup: env AGENTICCAD_WSL_DISTRO (default Ubuntu-22.04), AGENTICCAD_ROS_SETUP
(default /opt/ros/humble/setup.bash). Results go to spikes/results/t07.json.
"""
import json
import math
import os
import subprocess
from pathlib import Path

import numpy as np
import yourdfpy

PKG = Path(__file__).resolve().parent
URDF = PKG / "urdf" / "one_joint.urdf"
DISTRO = os.environ.get("AGENTICCAD_WSL_DISTRO", "Ubuntu-22.04")
ROS_SETUP = os.environ.get("AGENTICCAD_ROS_SETUP", "/opt/ros/humble/setup.bash")
Q = 0.5
T_JOINT = np.array([0.010, 0.0, 0.0169])


def expected_arm_pose(q):
    c, s = math.cos(q), math.sin(q)
    T = np.eye(4)
    T[:3, :3] = [[c, -s, 0], [s, c, 0], [0, 0, 1]]
    T[:3, 3] = T_JOINT
    return T


def wsl_path(p: Path) -> str:
    drive, rest = str(p).split(":", 1)
    return f"/mnt/{drive.lower()}{rest.replace(chr(92), '/')}"


def wsl(cmd: str, timeout=120) -> subprocess.CompletedProcess:
    script = f"source {ROS_SETUP} 2>/dev/null; {cmd}"
    return subprocess.run(["wsl", "-d", DISTRO, "-e", "bash", "-c", script],
                          capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")


def check_yourdfpy():
    def handler(fname):
        return str(PKG / fname.replace("package://urdf_1joint/", "")) if fname.startswith("package://") else fname

    robot = yourdfpy.URDF.load(str(URDF), filename_handler=handler, load_meshes=True, build_collision_scene_graph=True)
    robot.update_cfg({"j_shaft": Q})
    T = robot.get_transform("arm_link", "base_link")
    err = float(np.abs(T - expected_arm_pose(Q)).max())
    meshes = len(robot.scene.geometry)
    return {"status": "pass" if err < 1e-9 and meshes == 2 else "fail", "fk_max_abs_err": err,
            "meshes_loaded": meshes, "actuated": robot.actuated_joint_names}


def check_urdf_tool():
    r = wsl(f"command -v check_urdf >/dev/null || exit 127; check_urdf '{wsl_path(URDF)}'")
    if r.returncode == 127:
        return {"status": "unavailable", "detail": f"check_urdf not found in {DISTRO}"}
    ok = r.returncode == 0 and "Successfully Parsed XML" in r.stdout
    return {"status": "pass" if ok else "fail", "stdout": r.stdout.strip().splitlines()[-6:], "stderr": r.stderr.strip()[-500:]}


def check_gz_sdf():
    r = wsl(f"command -v gz >/dev/null || exit 127; gz sdf -p '{wsl_path(URDF)}'")
    if r.returncode == 127:
        return {"status": "unavailable", "detail": f"gz not found in {DISTRO}"}
    ok = r.returncode == 0 and "<sdf" in r.stdout and "j_shaft" in r.stdout
    return {"status": "pass" if ok else "fail", "stderr": r.stderr.strip()[-500:]}


def check_pinocchio():
    py = (
        "import sys, pinocchio as pin, numpy as np\n"
        f"m = pin.buildModelFromUrdf('{wsl_path(URDF)}'); d = m.createData()\n"
        f"q = np.array([{Q}]); pin.forwardKinematics(m, d, q)\n"
        "M = d.oMi[m.getJointId('j_shaft')]\n"
        "print(m.nq, list(M.translation), list(M.rotation.flatten()), m.effortLimit.tolist(), m.velocityLimit.tolist())\n"
    )
    r = wsl(f"python3 -c \"import pinocchio\" 2>/dev/null || exit 127; python3 - <<'EOF'\n{py}EOF")
    if r.returncode == 127:
        return {"status": "unavailable", "detail": f"pinocchio not importable in {DISTRO}"}
    if r.returncode != 0:
        return {"status": "fail", "stderr": r.stderr.strip()[-800:]}
    return {"status": "pass", "stdout": r.stdout.strip()}


def main():
    results = {}
    for name, fn in (("yourdfpy", check_yourdfpy), ("check_urdf", check_urdf_tool),
                     ("gz_sdf", check_gz_sdf), ("pinocchio", check_pinocchio)):
        try:
            results[name] = fn()
        except Exception as e:  # report, keep going
            results[name] = {"status": "fail", "error": f"{type(e).__name__}: {e}"}
        print(f"{name:11s} {results[name]['status']}")
    results["distro"] = DISTRO
    out = PKG.parent / "results" / "t07.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    return results


if __name__ == "__main__":
    main()
