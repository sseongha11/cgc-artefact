#!/usr/bin/env python3
"""Full factorial on the offline consensus gate, both domains, all panels.

Per domain and panel (five models and one mixed panel):
  A  no faults:  4 policies
  B  faults:     4 policies x 4 modes x 3 rates x 3 seeds
  C  RQ4:        MAJORITY, forgery attack on half the cases, 3 coordinators x 3 seeds
All runs use Rmax = 2: the action is binary, so a third round would repropose
the first action to the same (cached, deterministic) voters.

Verdicts are cached per (model, prompt), so the model is called once per
distinct (case, role, fault prompt); every other run reads the cache. Rates
are nested within a seed (a case poisoned at 0.10 is also poisoned at 0.50),
which keeps the number of distinct prompts small.

Outputs: results/full/<domain>/<panel>/<run>.jsonl and results/full/runs.csv
(one row of metrics per run). Finished runs are skipped, so the script
resumes after an interruption. It stops before a panel if the OpenRouter
balance is below --min-credit.

    ../.venv/bin/python full.py [--domains contract,planning] [--min-credit 5]
"""
import argparse
import csv
import json
import os
import statistics as st
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "agents"))

import requests  # noqa: E402
from run import build_parser, load_dotenv, run_experiment  # noqa: E402

CORPORA = {
    "contract": os.path.join(ROOT, "datasets", "contractnli", "corpus_balanced.jsonl"),
    "planning": os.path.join(ROOT, "harvester", "corpus_balanced.jsonl"),
}
MODELS = [
    "anthropic/claude-sonnet-5",
    "openai/gpt-6-sol",
    "google/gemini-3.8-flash",
    "deepseek/deepseek-v4.1-flash",
    "meta-llama/llama-4-maverick",
]
MIXED = "Org1MSP=openai/gpt-6-sol,Org2MSP=google/gemini-3.8-flash,Org3MSP=anthropic/claude-sonnet-5"
PANELS = [(m.split("/")[1], ["--model", m]) for m in MODELS] + [("mixed", ["--models", MIXED])]

POLICIES = ["ANY", "MAJORITY", "UNANIMOUS", "ROLE_WEIGHTED"]
MODES = ["prompt-poison", "sycophancy", "confident-wrong", "collusion"]
RATES = [0.10, 0.25, 0.50]
SEEDS = ["s1", "s2", "s3"]
COORDS = ["local", "orchestrator", "ledgerless"]
RMAX = 2


def conditions():
    for p in POLICIES:
        yield dict(policy=p, mode="none", rate=0.0, seed="s1", ledger="local", attack=0.0)
    for p in POLICIES:
        for m in MODES:
            for r in RATES:
                for s in SEEDS:
                    yield dict(policy=p, mode=m, rate=r, seed=s, ledger="local", attack=0.0)
    for c in COORDS:
        for s in SEEDS:
            yield dict(policy="MAJORITY", mode="none", rate=0.0, seed=s, ledger=c, attack=0.5)


def run_name(c):
    return f"{c['policy']}__{c['mode']}__{c['rate']:.2f}__{c['seed']}__{c['ledger']}__atk{c['attack']:.1f}"


def summarise(rs, c, domain, panel):
    faulty = [r for r in rs if r["poisoned_orgs"]]
    forged = [r for r in rs if r["attack_attempted"]]
    return {
        "domain": domain, "panel": panel, **c, "n": len(rs),
        "accuracy": st.mean(r["correct"] for r in rs),
        "commit_rate": st.mean(r["committed"] is not None for r in rs),
        "blast_all": st.mean(r["wrong_commit"] for r in rs),
        "blast_faulty": st.mean(r["wrong_commit"] for r in faulty) if faulty else "",
        "n_faulty": len(faulty),
        "rounds_mean": st.mean(r["rounds"] for r in rs),
        "abandon": st.mean(r["status"] != "COMMITTED" for r in rs),
        "ledger_writes_mean": st.mean(r["tx_writes"] for r in rs),
        "tokens_in_mean": st.mean(r["tokens_in"] for r in rs),
        "tokens_out_mean": st.mean(r["tokens_out"] for r in rs),
        "cost_per_case": st.mean(r["cost_usd"] for r in rs),
        "parse_failures": sum(r["parse_failures"] for r in rs),
        "forgery_attempted": len(forged),
        "forgery_succeeded": sum(r["attack_succeeded"] for r in forged),
        "wrong_in_forged": sum(r["attack_succeeded"] and r["wrong_commit"] for r in forged),
    }


def credit_left() -> float:
    d = requests.get("https://openrouter.ai/api/v1/credits",
                     headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
                     timeout=30).json()["data"]
    return d["total_credits"] - d["total_usage"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domains", default="contract,planning")
    ap.add_argument("--min-credit", type=float, default=5.0)
    ap.add_argument("--prefetch-workers", default="4")
    a = ap.parse_args()
    load_dotenv(os.path.join(ROOT, ".env"))

    out_root = os.path.join(ROOT, "results", "full")
    csv_path = os.path.join(out_root, "runs.csv")
    os.makedirs(out_root, exist_ok=True)
    conds = list(conditions())
    t0 = time.time()

    for domain in a.domains.split(","):
        for panel, model_args in PANELS:
            left = credit_left()
            print(f"== {domain} / {panel}  ({len(conds)} runs)  credit left ${left:.2f}", flush=True)
            if left < a.min_credit:
                print(f"stopping: credit below ${a.min_credit}", flush=True)
                return 3
            out_dir = os.path.join(out_root, domain, panel)
            os.makedirs(out_dir, exist_ok=True)
            for i, c in enumerate(conds, 1):
                path = os.path.join(out_dir, run_name(c) + ".jsonl")
                if os.path.exists(path):
                    continue
                argv = ["--corpus", CORPORA[domain], "--reviewer", "openrouter", *model_args,
                        "--policy", c["policy"], "--ledger", c["ledger"], "--seed", c["seed"],
                        "--max-rounds", str(RMAX), "--attack-rate", str(c["attack"]),
                        "--prefetch-workers", a.prefetch_workers]
                if c["mode"] != "none":
                    argv += ["--injection-mode", c["mode"], "--injection-rate", str(c["rate"])]
                rs = run_experiment(build_parser().parse_args(argv))
                with open(path + ".tmp", "w") as f:
                    f.writelines(json.dumps(r) + "\n" for r in rs)
                os.replace(path + ".tmp", path)
                row = summarise(rs, c, domain, panel)
                new = not os.path.exists(csv_path)
                with open(csv_path, "a", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=list(row))
                    if new:
                        w.writeheader()
                    w.writerow(row)
                if i % 25 == 0:
                    print(f"   {domain}/{panel}: {i}/{len(conds)} runs, {time.time() - t0:.0f}s", flush=True)
            print(f"   done {domain}/{panel}", flush=True)
    print(f"ALL DONE in {time.time() - t0:.0f}s, credit left ${credit_left():.2f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
