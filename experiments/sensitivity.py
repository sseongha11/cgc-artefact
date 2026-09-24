#!/usr/bin/env python3
"""Sensitivity of the contract results to the hypothesis filter.

The main contract corpus keeps only the four requirements whose rarer label is
at least 20% of annotations, a rule added after the pilot. This reruns the
key conditions on the unfiltered corpus (all 12 requirements with both labels,
balanced within each) with the two cheapest panels, to check whether the
filter changes the conclusions: the four policies without faults, and each
fault at rate 0.50 with one seed.

    ../.venv/bin/python sensitivity.py     # writes results/sensitivity/
"""
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "agents"))
sys.path.insert(0, HERE)

from full import RMAX, summarise  # noqa: E402
from run import build_parser, load_dotenv, run_experiment  # noqa: E402

CORPUS = os.path.join(ROOT, "datasets", "contractnli", "corpus_unfiltered.jsonl")
PANELS = [("deepseek-v4.1-flash", "deepseek/deepseek-v4.1-flash"), ("llama-4-maverick", "meta-llama/llama-4-maverick")]
POLICIES = ["ANY", "MAJORITY", "ROLE_WEIGHTED", "UNANIMOUS"]
MODES = ["prompt-poison", "confident-wrong", "sycophancy", "collusion"]


def main():
    load_dotenv(os.path.join(ROOT, ".env"))
    out_root = os.path.join(ROOT, "results", "sensitivity")
    csv_path = os.path.join(out_root, "runs.csv")
    for panel, model in PANELS:
        out_dir = os.path.join(out_root, panel)
        os.makedirs(out_dir, exist_ok=True)
        conds = [dict(policy=p, mode="none", rate=0.0) for p in POLICIES] + \
                [dict(policy=p, mode=m, rate=0.5) for p in POLICIES for m in MODES]
        for c in conds:
            name = f"{c['policy']}__{c['mode']}__{c['rate']:.2f}__s1__local__atk0.0"
            path = os.path.join(out_dir, name + ".jsonl")
            if os.path.exists(path):
                continue
            argv = ["--corpus", CORPUS, "--reviewer", "openrouter", "--model", model, "--policy", c["policy"],
                    "--ledger", "local", "--seed", "s1", "--max-rounds", str(RMAX), "--prefetch-workers", "4"]
            if c["mode"] != "none":
                argv += ["--injection-mode", c["mode"], "--injection-rate", str(c["rate"])]
            rs = run_experiment(build_parser().parse_args(argv))
            with open(path, "w") as f:
                f.writelines(json.dumps(r) + "\n" for r in rs)
            row = summarise(rs, {**c, "seed": "s1", "ledger": "local", "attack": 0.0}, "contract_unfiltered", panel)
            new = not os.path.exists(csv_path)
            with open(csv_path, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(row))
                if new:
                    w.writeheader()
                w.writerow(row)
        print(f"done {panel}", flush=True)


if __name__ == "__main__":
    main()
