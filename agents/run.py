#!/usr/bin/env python3
"""CGC agent runtime — run the consensus-gated workflow over the real corpus.

Loads the harvested planning-appeal corpus, runs each case through the 3-agent
LangGraph workflow under a chosen endorsement policy, optionally red-teaming an
agent, and reports the metrics the paper cares about: accuracy, blast radius,
conflict-resolution rounds, latency, and on-chain writes.

Offline demo (no cluster, no API key):
    python run.py --corpus ../harvester/corpus_balanced.jsonl \
                  --policy MAJORITY --reviewer mock --ledger local

Live run (needs the Fabric network + Node API + Claude):
    python run.py --corpus ... --policy UNANIMOUS --reviewer claude \
                  --ledger api --api-url http://localhost:4000 \
                  --org-users Org1MSP=appUser,Org2MSP=appUser,Org3MSP=appUser
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor

from graph import build_workflow, run_case
from ledger import LocalLedger, ApiLedger, MAJORITY, ANY, UNANIMOUS, ROLE_WEIGHTED
from coordinators import COORDINATORS
from redteam import attack_for, poison_for
from reviewer import make_reviewer, APPROVE, REJECT


def load_corpus(path: str, limit: int | None, run_id: str = "") -> list[dict]:
    cases = []
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if not r.get("clean_binary", True) or not r.get("label"):
                continue
            # Ledger key. '/'-separated, no ':' -> chaincode-safe. A run id namespaces
            # it so repeated runs on one ledger don't collide with decided steps.
            r["ref"] = r.get("ref") or r["appeal_ref"]   # stable id in every domain
            r["case_id"] = f"{run_id}/{r['ref']}" if run_id else r["ref"]
            r["payload_hash"] = r.get("fileid", "")
            cases.append(r)
    return cases[:limit] if limit else cases


def balanced_sample(cases: list[dict], n: int, seed: str) -> list[dict]:
    """n cases, half per label, chosen by a seeded hash so every run and model sees
    the same sample (the corpus file is in seed order, not shuffled)."""
    import hashlib
    rank = lambda c: hashlib.sha256(f"{seed}:{c['ref']}".encode()).hexdigest()
    out = []
    for label in (APPROVE, REJECT):
        out += sorted((c for c in cases if c["label"] == label), key=rank)[: n // 2]
    return sorted(out, key=rank)


def load_dotenv(path: str) -> None:
    """Read KEY=VALUE lines from the repo .env (git ignored) into the environment."""
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.removeprefix("export ").split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def prefetch(reviewer, cases, poisons, workers: int) -> None:
    """Fill the reviewer cache in parallel; the gated run then only reads it."""
    orgs = ["Org1MSP", "Org2MSP", "Org3MSP"]
    jobs = [(c, o, poisons[c["case_id"]].get(o)) for c in cases for o in orgs]
    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(lambda j: reviewer.review(*j), jobs))


def build_ledger(args):
    if args.ledger == "local":
        return LocalLedger()
    if args.ledger in COORDINATORS:
        return COORDINATORS[args.ledger]()
    users = dict(kv.split("=", 1) for kv in args.org_users.split(","))
    gateways = dict(kv.split("=", 1) for kv in args.gateways.split(",")) if args.gateways else None
    return ApiLedger(args.api_url, users, gateways=gateways)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="CGC consensus-gated agent runtime")
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--policy", default=MAJORITY, choices=[ANY, MAJORITY, UNANIMOUS, ROLE_WEIGHTED])
    ap.add_argument("--reviewer", default="mock", choices=["mock", "claude", "openrouter"])
    ap.add_argument("--models", default="",
                    help="openrouter: one model per org, e.g. Org1MSP=a,Org2MSP=b,Org3MSP=c")
    ap.add_argument("--prefetch-workers", type=int, default=8,
                    help="openrouter: parallel calls used to fill the cache before the run")
    ap.add_argument("--ledger", default="local", choices=["local", "api", *COORDINATORS],
                    help="local/api: consensus gated (offline gate / Fabric); "
                         "orchestrator, ledgerless: baselines")
    ap.add_argument("--api-url", default="http://localhost:4000")
    ap.add_argument("--gateways", default="",
                    help="per org gateway URLs, e.g. Org1MSP=http://localhost:4001,...")
    ap.add_argument("--attack-mode", default="forge", choices=["forge", "omit"],
                    help="forge: Org1 casts a dissenter's vote as an endorsement; "
                         "omit: Org2, running the coordinator, drops another org's endorsement")
    ap.add_argument("--attack-rate", type=float, default=0.0,
                    help="fraction of cases where the operator (Org1) forges a dissenting vote")
    ap.add_argument("--org-users", default="Org1MSP=appUser,Org2MSP=appUser,Org3MSP=appUser")
    ap.add_argument("--accuracy", type=float, default=0.75, help="mock: per-agent accuracy")
    ap.add_argument("--model", default="claude-sonnet-5",
                    help="claude: Anthropic model id; openrouter: model for every org")
    ap.add_argument("--injection-rate", type=float, default=0.0)
    ap.add_argument("--injection-mode", default="none",
                    choices=["none", "prompt-poison", "sycophancy", "confident-wrong", "collusion"])
    ap.add_argument("--seed", default="rt")
    ap.add_argument("--max-rounds", type=int, default=1)
    ap.add_argument("--mandatory-msp", default="Org3MSP", help="ROLE_WEIGHTED mandatory org")
    ap.add_argument("--required-weight", type=int, default=2, help="ROLE_WEIGHTED threshold")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sample", type=int, default=None, help="balanced seeded sample of N cases")
    ap.add_argument("--out", default=None, help="write per-case results JSONL")
    ap.add_argument("--run-id", default="", help="namespace on-chain case keys (needed for repeat runs on one ledger)")
    return ap


def run_experiment(args) -> list[dict]:
    """Run every selected case under one condition; return per case results."""
    cases = load_corpus(args.corpus, args.limit, args.run_id)
    if args.sample:
        cases = balanced_sample(cases, args.sample, "sample")
    if not cases:
        raise SystemExit("no usable cases in corpus")

    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
    models = dict(kv.split("=", 1) for kv in args.models.split(",")) if args.models else {}
    reviewer = make_reviewer(args.reviewer, accuracy=args.accuracy, model=args.model, models=models)
    ledger = build_ledger(args)
    ledger.set_policy(args.policy, 3, args.mandatory_msp, args.required_weight)
    app = build_workflow()

    poisons = {c["case_id"]: poison_for(c["ref"], args.injection_rate, args.injection_mode, args.seed)
               for c in cases}
    if args.reviewer == "openrouter":
        prefetch(reviewer, cases, poisons, args.prefetch_workers)

    results = []
    for case in cases:
        # Seeded on the appeal ref, not the ledger key, so injection is identical across runs.
        poison = poisons[case["case_id"]]
        attack = attack_for(case["ref"], args.attack_rate, args.seed)
        results.append(run_case(app, case, reviewer, ledger, poison, args.max_rounds, attack,
                                args.attack_mode))
    return results


def main() -> int:
    args = build_parser().parse_args()
    results = run_experiment(args)
    report(results, args)
    if args.out:
        with open(args.out, "w") as f:
            for r in results:
                f.write(json.dumps(r) + "\n")
        print(f"\nper-case results -> {args.out}")
    return 0


def report(results: list[dict], args) -> None:
    n = len(results)
    committed = [r for r in results if r["committed"] is not None]
    correct = sum(r["correct"] for r in results)
    wrong_commits = sum(r["wrong_commit"] for r in results)
    poisoned = [r for r in results if r["poisoned_orgs"]]
    clean = [r for r in results if not r["poisoned_orgs"]]

    # single-agent baseline: the proposer (Org1) deciding alone
    solo_correct = sum(r["verdicts"].get("Org1MSP") == r["ground_truth"] for r in results)

    # confusion on committed decisions (APPROVE = positive)
    tp = sum(r["committed"] == APPROVE and r["ground_truth"] == APPROVE for r in committed)
    fp = sum(r["committed"] == APPROVE and r["ground_truth"] == REJECT for r in committed)
    fn = sum(r["committed"] == REJECT and r["ground_truth"] == APPROVE for r in committed)
    prec = tp / (tp + fp) if tp + fp else float("nan")
    rec = tp / (tp + fn) if tp + fn else float("nan")

    lat = [r["latency_ms"] for r in results]
    rounds = [r["rounds"] for r in results]

    print("=" * 60)
    print(f"CGC run · policy={args.policy} · reviewer={args.reviewer} · ledger={args.ledger}"
          f" · attack={args.attack_rate}")
    print(f"        · injection={args.injection_mode}@{args.injection_rate} · cases={n}")
    print("=" * 60)
    print(f"  committed / rejected(no-commit) : {len(committed)} / {n - len(committed)}")
    print(f"  ACCURACY (right action committed): {correct}/{n} = {correct/n:.1%}")
    print(f"  single-agent (Org1 alone)        : {solo_correct}/{n} = {solo_correct/n:.1%}")
    print(f"  BLAST RADIUS (wrong committed)   : {wrong_commits}/{n} = {wrong_commits/n:.1%}")
    if poisoned:
        pab = sum(r["wrong_commit"] for r in poisoned)
        print(f"    on poisoned cases              : {pab}/{len(poisoned)} = {pab/len(poisoned):.1%}")
    if clean:
        cab = sum(r["wrong_commit"] for r in clean)
        print(f"    on clean cases                 : {cab}/{len(clean)} = {cab/len(clean):.1%}")
    print(f"  precision / recall (APPROVE)     : {prec:.2f} / {rec:.2f}")
    print(f"  conflict rounds  mean/max        : {statistics.mean(rounds):.2f} / {max(rounds)}")
    print(f"  latency ms  p50/p95              : {int(statistics.median(lat))} / "
          f"{int(sorted(lat)[int(0.95*len(lat))-1])}")
    print(f"  ledger writes  total/mean        : {sum(r['tx_writes'] for r in results)} / "
          f"{statistics.mean(r['tx_writes'] for r in results):.1f}")
    if args.reviewer != "mock":
        print(f"  models                           : {sorted(set(m for r in results for m in r['models'].values()))}")
        print(f"  tokens in/out per case (mean)    : {statistics.mean(r['tokens_in'] for r in results):.0f} / "
              f"{statistics.mean(r['tokens_out'] for r in results):.0f}")
        print(f"  cost of the calls (USD)          : {sum(r['cost_usd'] for r in results):.4f}")
        print(f"  unparseable replies              : {sum(r['parse_failures'] for r in results)}")
    attempted = [r for r in results if r.get("attack_attempted")]
    if attempted:
        ok = sum(r["attack_succeeded"] for r in attempted)
        label = "OMISSION" if args.attack_mode == "omit" else "FORGERY "
        flipped = sum(r["attack_succeeded"] and r["wrong_commit"] for r in attempted)
        print(f"  {label} succeeded / attempted   : {ok}/{len(attempted)} "
              f"(wrong commits in attacked cases: {flipped})")


if __name__ == "__main__":
    raise SystemExit(main())
