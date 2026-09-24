#!/usr/bin/env python3
"""Pilot: 20 balanced cases, 5 models plus a mixed panel, offline consensus gate.

    ../.venv/bin/python pilot.py planning|contract

For each panel it runs the 4 policies with no fault, and MAJORITY with prompt
poisoning and with overconfidence injected into one agent on every case.
Verdicts are cached per (model, prompt), so only the first policy of a
condition calls the model; the rest reuse the cache.

Then: ../.venv/bin/python pilot_report.py planning|contract
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = os.path.join(ROOT, ".venv", "bin", "python")
CORPORA = {
    "planning": os.path.join(ROOT, "harvester", "corpus_balanced.jsonl"),
    "contract": os.path.join(ROOT, "datasets", "contractnli", "corpus_balanced.jsonl"),
}
N = 20

MODELS = [
    "anthropic/claude-sonnet-5",
    "openai/gpt-6-sol",
    "google/gemini-3.8-flash",
    "deepseek/deepseek-v4.1-flash",
    "meta-llama/llama-4-maverick",
]
# Mixed panel: a different provider per organisation (answers come from the cache).
MIXED = "Org1MSP=openai/gpt-6-sol,Org2MSP=google/gemini-3.8-flash,Org3MSP=anthropic/claude-sonnet-5"

PANELS = [(m.split("/")[1], ["--model", m]) for m in MODELS] + [("mixed", ["--models", MIXED])]
CONDITIONS = [(p, "none") for p in ("ANY", "MAJORITY", "UNANIMOUS", "ROLE_WEIGHTED")] + \
             [("MAJORITY", "prompt-poison"), ("MAJORITY", "confident-wrong")]


def main() -> int:
    domain = sys.argv[1] if len(sys.argv) > 1 else "planning"
    CORPUS = CORPORA[domain]
    OUT = os.path.join(ROOT, "results", "pilot", domain)
    os.makedirs(OUT, exist_ok=True)
    for name, model_args in PANELS:
        for policy, mode in CONDITIONS:
            tag = f"{name}__{policy}__{mode}"
            args = [PY, "run.py", "--corpus", CORPUS, "--sample", str(N), "--policy", policy,
                    "--reviewer", "openrouter", *model_args, "--ledger", "local",
                    "--out", os.path.join(OUT, f"{tag}.jsonl"),
                    # each in flight request reserves credit for its maximum output
                    "--prefetch-workers", os.environ.get("PREFETCH_WORKERS", "8")]
            if mode != "none":
                args += ["--injection-mode", mode, "--injection-rate", "1.0"]
            print(f"── {tag}", flush=True)
            done = subprocess.run(args, cwd=os.path.join(ROOT, "agents"), capture_output=True, text=True)
            with open(os.path.join(OUT, f"{tag}.txt"), "w") as f:
                f.write(done.stdout + done.stderr)
            if done.returncode != 0:
                print(done.stderr[-2000:], file=sys.stderr)
                return done.returncode
            cost = [l for l in done.stdout.splitlines() if "cost of the calls" in l or "ACCURACY" in l]
            print("   " + "\n   ".join(x.strip() for x in cost), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
