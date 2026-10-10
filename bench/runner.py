"""Bench runner (T1.11, P1 §5): every pack's bench.yaml + bench/core.yaml through the agents, one job per case.

    jobs/bench/<run>/<case id>/       the job directories (agents.job.run)
    bench/results/<run>.json          metrics per case + summary, <run> = <date>_<config>

Metrics per case: status and reason; spec match against the case's target Spec; pass/fail per pipeline
stage of the last round and the gates of the open failures; DoF-0 rate (sketches with DoF 0 / all sketches
over every compile that got that far); compiles, sessions and escalations per part; pipeline rounds and
design rollbacks; tokens, cost and wall time. The P1 exit criterion is ≥ 4/5 pack cases and 2/2 core cases.
"""
import json
import time
from pathlib import Path
from typing import Callable

import yaml

import packs
from agents import stages
from agents.job import Budgets, run
from schemas.plan import Spec

ROOT = Path(__file__).resolve().parents[1]
CORE = Path(__file__).resolve().parent / "core.yaml"
RESULTS = Path(__file__).resolve().parent / "results"
EXIT = {"pack": (4, 5), "core": (2, 2)}   # P1 exit: passes needed / cases


def cases() -> list[dict]:
    """[{id, group, pack, prompt, spec}] from every pack's bench.yaml, then the core-only cases."""
    out = [dict(c, group="pack", pack=p) for p in packs.available() for c in packs.bench(p)]
    out += [dict(c, group="core", pack=None) for c in yaml.safe_load(CORE.read_text(encoding="utf-8"))]
    return out


def spec_match(target: dict, spec: dict | None, bom: dict | None) -> dict:
    """Compare the agent's Spec with the case target. components_requested: every target card must be in the
    BOM (the resolver adds the default servo); electronics compared as a set; notes are not compared."""
    if spec is None:
        return {"ok": False, "diffs": ["no spec"]}
    want = Spec.model_validate(target).model_dump()
    diffs = []
    for k in target:
        if k in ("notes", "components_requested", "electronics"):
            continue
        if want[k] != spec.get(k):
            diffs.append("%s: want %r, got %r" % (k, want[k], spec.get(k)))
    missing = [c for c in want["components_requested"] if c not in (bom or {}).get("cards", [])]
    if missing:
        diffs.append("components_requested: %s not in the BOM" % missing)
    if "electronics" in target:
        e_want, e_got = want["electronics"], spec.get("electronics")
        if (sorted(e_want) if isinstance(e_want, list) else e_want) != (sorted(e_got) if isinstance(e_got, list) else e_got):
            diffs.append("electronics: want %r, got %r" % (e_want, e_got))
    return {"ok": not diffs, "diffs": diffs}


def _json(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def summarize(case: dict, job_dir: Path) -> dict:
    """Metrics of one finished job directory (also usable on old jobs: missing files give empty fields)."""
    job_dir = Path(job_dir)
    res = _json(job_dir / "result.json") or {"status": "missing", "reason": "no result.json"}
    rep = _json(job_dir / "out" / "report.json") or {}
    compiles = [json.loads(l) for l in (job_dir / "compiles.jsonl").read_text(encoding="utf-8").splitlines()
                if l.strip()] if (job_dir / "compiles.jsonl").is_file() else []
    n_sk = sum(c["n_sketches"] for c in compiles)
    totals = res.get("totals") or {}
    return {
        "id": case["id"], "group": case["group"], "pack": case["pack"], "job_dir": str(job_dir),
        "status": res.get("status"), "reason": res.get("reason", ""),
        "spec_match": spec_match(case.get("spec") or {}, _json(job_dir / "spec.json"), _json(job_dir / "bom.json")),
        "stages": {k: v.get("ok") for k, v in (rep.get("stages") or {}).items()},
        "failed_stage": rep.get("failed_stage"),
        "open_failure_gates": sorted({s.get("gate", "?") for f in res.get("open_failures") or [] if isinstance(f, dict)
                                      for s in f.get("feedback") or [f] if isinstance(s, dict)}),
        "dof0_rate": round(sum(c["n_dof0"] for c in compiles) / n_sk, 4) if n_sk else None,
        "compiles": len(compiles), "compile_failures_by_gate": _count(g for c in compiles for g in c["gates"]),
        "parts": res.get("parts") or {},
        "rounds": res.get("rounds", 0), "rollbacks": res.get("rollbacks", 0),
        "tokens_in": totals.get("tokens_in", 0), "tokens_out": totals.get("tokens_out", 0),
        "cost_usd": totals.get("cost_usd", 0.0), "sessions": totals.get("sessions", 0),
        "wall_s": res.get("elapsed_s", 0.0),
    }


def _count(items) -> dict:
    out: dict = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return dict(sorted(out.items()))


def summary(rows: list[dict]) -> dict:
    out = {"cases": len(rows), "passed": sum(r["status"] == "pass" for r in rows),
           "cost_usd": round(sum(r["cost_usd"] for r in rows), 4), "wall_s": round(sum(r["wall_s"] for r in rows), 1),
           "tokens_in": sum(r["tokens_in"] for r in rows), "tokens_out": sum(r["tokens_out"] for r in rows),
           "spec_match": sum(r["spec_match"]["ok"] for r in rows)}
    sk = [r["dof0_rate"] for r in rows if r["dof0_rate"] is not None]
    out["dof0_rate_mean"] = round(sum(sk) / len(sk), 4) if sk else None
    exit_ok = True
    for g, (need, total) in EXIT.items():
        rs = [r for r in rows if r["group"] == g]
        p = sum(r["status"] == "pass" for r in rs)
        out[g] = {"passed": p, "ran": len(rs), "need": "%d/%d" % (need, total)}
        exit_ok &= len(rs) == total and p >= need
    out["p1_exit_bench"] = exit_ok      # only meaningful on a full run
    return out


def run_bench(selected: list[dict], run_dir: Path, backends_for: Callable[[Path], stages.Backends],
              config: str, budgets: Budgets | None = None, description: bool = True, reuse: bool = False,
              results: Path | None = None, log: Callable[[str], None] = print) -> dict:
    """Run each case into run_dir/<id> (reuse: keep cases that already have a result.json), write the results."""
    run_dir = Path(run_dir).resolve()
    rows = []
    for case in selected:
        job = run_dir / case["id"]
        if reuse and (job / "result.json").is_file():
            log("== %s: reusing %s" % (case["id"], job))
        else:
            log("== %s: %s" % (case["id"], case["prompt"]))
            run(case["prompt"], job, backends_for(job), approve=lambda t: True, budgets=budgets or Budgets(),
                description=description, log=lambda m, i=case["id"]: log("[%s] %s" % (i, m)))
        rows.append(summarize(case, job))
        r = rows[-1]
        log("== %s: %s (%s); spec %s; $%.3f, %.0f s" % (r["id"], r["status"], r["reason"],
                                                        "ok" if r["spec_match"]["ok"] else r["spec_match"]["diffs"],
                                                        r["cost_usd"], r["wall_s"]))
    doc = {"run": run_dir.name, "config": config, "date": time.strftime("%Y-%m-%d %H:%M:%S"),
           "description_checks": description, "summary": summary(rows), "cases": rows}
    path = Path(results or RESULTS) / ("%s.json" % run_dir.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    log("== bench %s: %d/%d pass, $%.2f, %.0f s -> %s" % (run_dir.name, doc["summary"]["passed"], len(rows),
                                                           doc["summary"]["cost_usd"], doc["summary"]["wall_s"], path))
    return doc
