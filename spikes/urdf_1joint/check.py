"""T0.7 checks for the 1-joint URDF package.

    uv run python -m spikes.urdf_1joint.check

  yourdfpy   load (package:// resolved to this dir), FK at q = 0.5 rad vs. origin * Rz(q)  [Windows venv]
  check_urdf urdfdom parser                                                             [Linux]
  gz sdf -p  URDF -> SDF conversion                                                     [Linux, Gazebo]
  pinocchio  buildModel, FK at q = 0.5 rad                                              [Linux python]
A tool that is not installed is reported as "unavailable", never as a pass.
Linux runner: env AGENTICCAD_LINUX = "docker" (default; image AGENTICCAD_ROS_IMAGE, default
agenticcad-ros:jazzy from docker/ros-tools) or "wsl" (distro AGENTICCAD_WSL_DISTRO, default Ubuntu-22.04).
The package dir is mounted read-only at /pkg. Results go to spikes/results/t07.json.
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
RUNNER = os.environ.get("AGENTICCAD_LINUX", "docker")
IMAGE = os.environ.get("AGENTICCAD_ROS_IMAGE", "agenticcad-ros:jazzy")
DISTRO = os.environ.get("AGENTICCAD_WSL_DISTRO", "Ubuntu-22.04")
ROS_SETUP = "/opt/ros/jazzy/setup.bash" if RUNNER == "docker" else "/opt/ros/humble/setup.bash"
Q = 0.5
T_JOINT = np.array([0.010, 0.0, 0.0169])


def expected_arm_pose(q):
    c, s = math.cos(q), math.sin(q)
    T = np.eye(4)
    T[:3, :3] = [[c, -s, 0], [s, c, 0], [0, 0, 1]]
    T[:3, 3] = T_JOINT
    return T


def lpath(p: Path) -> str:
    """Path of a package file as seen by the Linux runner."""
    if RUNNER == "docker":
        return "/pkg/" + p.relative_to(PKG).as_posix()
    drive, rest = str(p).split(":", 1)
    return f"/mnt/{drive.lower()}{rest.replace(chr(92), '/')}"


def linux(cmd: str, timeout=180) -> subprocess.CompletedProcess:
    script = f"source {ROS_SETUP} 2>/dev/null; {cmd}"
    if RUNNER == "docker":
        argv = ["docker", "run", "--rm", "-i", "-v", f"{PKG}:/pkg:ro", IMAGE, "bash", "-c", script]
    else:
        argv = ["wsl", "-d", DISTRO, "-e", "bash", "-c", script]
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")


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
    r = linux(f"command -v check_urdf >/dev/null || exit 127; check_urdf '{lpath(URDF)}'")
    if r.returncode == 127:
        return {"status": "unavailable", "detail": f"check_urdf not found in {RUNNER}"}
    ok = r.returncode == 0 and "Successfully Parsed XML" in r.stdout
    return {"status": "pass" if ok else "fail", "stdout": r.stdout.strip().splitlines()[-6:], "stderr": r.stderr.strip()[-500:]}


def check_gz_sdf():
    r = linux(f"command -v gz >/dev/null || exit 127; gz sdf -p '{lpath(URDF)}'")
    if r.returncode == 127:
        return {"status": "unavailable", "detail": f"gz not found in {RUNNER}"}
    ok = r.returncode == 0 and "<sdf" in r.stdout and "j_shaft" in r.stdout
    return {"status": "pass" if ok else "fail", "stderr": r.stderr.strip()[-500:]}


def check_pinocchio():
    py = (
        "import json, pinocchio as pin, numpy as np\n"
        f"m = pin.buildModelFromUrdf('{lpath(URDF)}'); d = m.createData()\n"
        f"q = np.array([{Q}]); pin.forwardKinematics(m, d, q)\n"
        "M = d.oMi[m.getJointId('j_shaft')]\n"
        "print(json.dumps({'nq': m.nq, 'T': M.homogeneous.tolist(), 'effort': m.effortLimit.tolist(),\n"
        "                  'velocity': m.velocityLimit.tolist(), 'mass': [i.mass for i in m.inertias]}))\n"
    )
    r = linux(f"python3 -c \"import pinocchio\" 2>/dev/null || exit 127; python3 - <<'EOF'\n{py}EOF")
    if r.returncode == 127:
        return {"status": "unavailable", "detail": f"pinocchio not importable in {RUNNER}"}
    if r.returncode != 0:
        return {"status": "fail", "stderr": r.stderr.strip()[-800:]}
    out = json.loads(r.stdout.strip().splitlines()[-1])
    out["fk_max_abs_err"] = float(np.abs(np.array(out.pop("T")) - expected_arm_pose(Q)).max())
    ok = (out["nq"] == 1 and out["fk_max_abs_err"] < 1e-9
          and abs(out["effort"][0] - 0.54) < 1e-9 and abs(out["velocity"][0] - 7.0) < 1e-9)
    return {"status": "pass" if ok else "fail", **out}


def main():
    results = {}
    for name, fn in (("yourdfpy", check_yourdfpy), ("check_urdf", check_urdf_tool),
                     ("gz_sdf", check_gz_sdf), ("pinocchio", check_pinocchio)):
        try:
            results[name] = fn()
        except Exception as e:  # report, keep going
            results[name] = {"status": "fail", "error": f"{type(e).__name__}: {e}"}
        print(f"{name:11s} {results[name]['status']}")
    results["runner"] = IMAGE if RUNNER == "docker" else DISTRO
    out = PKG.parent / "results" / "t07.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    return results


if __name__ == "__main__":
    main()
