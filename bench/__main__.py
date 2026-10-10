"""CLI: uv run python -m bench run [--ids B1-1,B1-6] [--config agent_sdk] [--run <name>] [--reuse] [--no-description]
         uv run python -m bench list

Jobs go to jobs/bench/<run>/<id>/, results to bench/results/<run>.json; <run> defaults to <date>_<config>.
--reuse continues an interrupted run (cases with a result.json are summarized, not rerun).
"""
import argparse
import sys
import time
from pathlib import Path

from agents.__main__ import backends_for
from agents.job import Budgets
from bench.runner import ROOT, cases, run_bench


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bench")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    r = sub.add_parser("run")
    r.add_argument("--ids", help="comma-separated case ids (default: all)")
    r.add_argument("--backend", choices=["agent_sdk", "api"], default="agent_sdk")
    r.add_argument("--config", help="label in the run name and results (default: the backend)")
    r.add_argument("--run", help="run name (default: <date>_<config>)")
    r.add_argument("--reuse", action="store_true")
    r.add_argument("--no-description", action="store_true", help="skip D1/D2/D4 (no Docker)")
    a = ap.parse_args(argv)
    all_cases = cases()
    if a.cmd == "list":
        for c in all_cases:
            print("%-6s %-5s %s" % (c["id"], c["group"], c["prompt"]))
        return 0
    want = [i.strip() for i in a.ids.split(",")] if a.ids else [c["id"] for c in all_cases]
    unknown = sorted(set(want) - {c["id"] for c in all_cases})
    if unknown:
        ap.error("unknown case ids %s" % unknown)
    config = a.config or a.backend
    run_dir = ROOT / "jobs" / "bench" / (a.run or "%s_%s" % (time.strftime("%Y%m%d"), config))
    doc = run_bench([c for c in all_cases if c["id"] in want], run_dir,
                    lambda job: backends_for(a.backend, job), config, Budgets(),
                    description=not a.no_description, reuse=a.reuse)
    return 0 if doc["summary"]["passed"] == doc["summary"]["cases"] else 1


if __name__ == "__main__":
    sys.exit(main())
