"""CLI: uv run python -m agents run "<prompt>" [--yes] [--job jobs/<id>] [--no-description]

Model routing (plan §2.3): Opus for requirements and design, Sonnet for part agents with one escalation
to Opus. Backend: the Agent SDK on the user's subscription (D4); --backend api for the Claude API.
"""
import argparse
import sys
import time
from pathlib import Path

from agents.job import Budgets, run, slug

OPUS, SONNET = "claude-opus-5-5", "claude-sonnet-5-5"
ROLES = {"requirements": (OPUS, 8, 1.0), "design": (OPUS, 40, 5.0), "part": (SONNET, 30, 2.0),
         "part_escalated": (OPUS, 30, 3.0)}   # model, max turns, per-session budget (USD, Agent SDK only)


def backends_for(kind: str, cwd: Path):
    def make(role: str):
        model, turns, usd = ROLES[role]
        if kind == "api":
            from llm.claude_api_backend import ClaudeAPIBackend
            return ClaudeAPIBackend(model=model, max_turns=turns, max_tokens=16000)
        from llm.agent_sdk_backend import AgentSDKBackend
        return AgentSDKBackend(model=model, max_turns=turns, cwd=str(cwd), max_budget_usd=usd)
    return make


def console_approve(text: str):
    print(text)
    ans = input("Approve? [y = go / n = abort / anything else = change request]: ").strip()
    return True if ans.lower() in ("y", "yes") else False if ans.lower() in ("n", "no", "") else ans


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agents")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("prompt")
    r.add_argument("--job", type=Path)
    r.add_argument("--yes", action="store_true", help="approve the checkpoint automatically")
    r.add_argument("--backend", choices=["agent_sdk", "api"], default="agent_sdk")
    r.add_argument("--no-description", action="store_true", help="skip D1/D2/D4 (no Docker)")
    a = ap.parse_args(argv)
    job = a.job or Path("jobs") / ("%s_%s" % (time.strftime("%Y%m%d_%H%M%S"), slug(a.prompt, 30)))
    res = run(a.prompt, job, backends_for(a.backend, job.resolve()),
              approve=(lambda t: (print(t), True)[1]) if a.yes else console_approve,
              budgets=Budgets(), description=not a.no_description)
    return 0 if res.status == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
