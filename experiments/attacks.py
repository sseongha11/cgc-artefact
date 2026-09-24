#!/usr/bin/env python3
"""Coordinator attacks for RQ4, on stored answers (no LLM calls).

For every panel and seed, the operator of the coordination infrastructure
attacks half of the contract cases under MAJORITY, against each coordinator:
  forge  Org1 casts an endorsement in a dissenting organisation's name
         (already produced by full.py; not rerun here)
  omit   Org2, running the coordinator, silently drops another organisation's
         endorsement after the votes arrive
Outputs sit next to the full run's files, ending in __omit0.5.jsonl.

    ../.venv/bin/python attacks.py [--domain contract]
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "agents"))

from full import CORPORA, PANELS, SEEDS, COORDS, RMAX  # noqa: E402
from run import build_parser, load_dotenv, run_experiment  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default="contract")
    a = ap.parse_args()
    load_dotenv(os.path.join(ROOT, ".env"))
    for panel, model_args in PANELS:
        out_dir = os.path.join(ROOT, "results", "full", a.domain, panel)
        for coord in COORDS:
            for seed in SEEDS:
                path = os.path.join(out_dir, f"MAJORITY__none__0.00__{seed}__{coord}__omit0.5.jsonl")
                if os.path.exists(path):
                    continue
                argv = ["--corpus", CORPORA[a.domain], "--reviewer", "openrouter", *model_args,
                        "--policy", "MAJORITY", "--ledger", coord, "--seed", seed,
                        "--max-rounds", str(RMAX), "--attack-rate", "0.5", "--attack-mode", "omit"]
                rs = run_experiment(build_parser().parse_args(argv))
                with open(path, "w") as f:
                    f.writelines(json.dumps(r) + "\n" for r in rs)
        print(f"done {a.domain}/{panel}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
