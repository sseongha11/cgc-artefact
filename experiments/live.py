#!/usr/bin/env python3
"""Live Fabric subset: latency, ledger writes, bytes on the ledger, audit.

Runs a balanced sample of contract cases through the live network under each
policy, using the cached answers of the Sonnet panel (real decisions, no LLM
latency or spend), so the timing isolates the cost of coordination. The same
cases also run through the in memory coordinators for comparison, and the
forgery attack runs against the live network. Every committed case's trail
is read back from the ledger to check audit completeness.

Needs the testbed (deploy/scripts/up.sh). Port forwards to the three
gateways are opened here.

    ../.venv/bin/python live.py [--sample 20]
"""
import argparse
import json
import os
import statistics as st
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "agents"))

import requests  # noqa: E402
from run import build_parser, run_experiment  # noqa: E402

CORPUS = os.path.join(ROOT, "datasets", "contractnli", "corpus_balanced.jsonl")
MODEL = "anthropic/claude-sonnet-5"
POLICIES = ["ANY", "MAJORITY", "UNANIMOUS", "ROLE_WEIGHTED"]
ORGS = ["Org1MSP", "Org2MSP", "Org3MSP"]
USERS = "Org1MSP=agent-org1,Org2MSP=agent-org2,Org3MSP=agent-org3"
BASE = 4000
OUT = os.path.join(ROOT, "results", "live")


def gw(i):
    return f"http://localhost:{BASE + i}"


def open_gateways():
    procs = [subprocess.Popen(["kubectl", "-n", "cgc", "port-forward", f"svc/gateway-org{i}", f"{BASE + i}:4000"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for i in (1, 2, 3)]
    for i in (1, 2, 3):
        for _ in range(30):
            try:
                requests.get(gw(i), timeout=2)
                break
            except requests.exceptions.ConnectionError:
                time.sleep(1)
    for i in (1, 2, 3):  # idempotent: the CA re-enrols identities it already knows
        requests.post(f"{gw(i)}/register", json={"org": f"Org{i}MSP", "userId": f"agent-org{i}"},
                      timeout=120).raise_for_status()
    return procs


def trail(case_id):
    r = requests.get(f"{gw(1)}/caseTrail", params={"org": "Org1MSP", "userId": "agent-org1",
                                                   "data[caseID]": case_id}, timeout=60)
    r.raise_for_status()
    return r.json()


def audit(result, policy):
    """A committed decision is complete if the ledger alone gives its proposer,
    all three votes, its round, its policy and its outcome."""
    t = trail(result["case_id"])
    step = t[0] if t else {}
    ok = (step.get("status") == "COMMITTED" and step.get("outcome") == result["committed"]
          and step.get("policy") == policy and step.get("round") == result["rounds"]
          and bool(step.get("proposer")) and sorted(step.get("votes", {})) == ORGS)
    return ok, len(json.dumps(t).encode())


def run(argv):
    return run_experiment(build_parser().parse_args(argv))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=20)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    common = ["--corpus", CORPUS, "--sample", str(a.sample), "--reviewer", "openrouter", "--model", MODEL,
              "--max-rounds", "2", "--prefetch-workers", "2"]
    live = ["--ledger", "api", "--api-url", gw(1), "--org-users", USERS,
            "--gateways", ",".join(f"Org{i}MSP={gw(i)}" for i in (1, 2, 3))]
    procs = open_gateways()
    summary = []
    try:
        for policy in POLICIES:
            rs = run(common + live + ["--policy", policy, "--run-id", f"live-{stamp}-{policy}"])
            audits = [audit(r, policy) for r in rs if r["committed"] is not None]
            row = {
                "coordinator": "CGC (Fabric)", "policy": policy, "n": len(rs),
                "latency_p50_s": st.median(r["latency_ms"] for r in rs) / 1000,
                "latency_p95_s": sorted(r["latency_ms"] for r in rs)[int(0.95 * len(rs)) - 1] / 1000,
                "writes_mean": st.mean(r["tx_writes"] for r in rs),
                "s_per_write": st.mean(r["latency_ms"] / r["tx_writes"] for r in rs if r["tx_writes"]) / 1000,
                "rounds_mean": st.mean(r["rounds"] for r in rs),
                "accuracy": st.mean(r["correct"] for r in rs),
                "audit_complete": f"{sum(ok for ok, _ in audits)}/{len(audits)}",
                "trail_bytes_mean": st.mean(b for _, b in audits) if audits else 0,
            }
            summary.append(row)
            print(json.dumps(row), flush=True)
            with open(os.path.join(OUT, f"{policy}.jsonl"), "w") as f:
                f.writelines(json.dumps(r) + "\n" for r in rs)

        rs = run(common + live + ["--policy", "MAJORITY", "--attack-rate", "0.5",
                                  "--run-id", f"live-{stamp}-forge"])
        tried = [r for r in rs if r["attack_attempted"]]
        row = {"coordinator": "CGC (Fabric)", "policy": "MAJORITY", "attack": "forgery on half",
               "forgery": f"{sum(r['attack_succeeded'] for r in tried)}/{len(tried)} accepted"}
        summary.append(row)
        print(json.dumps(row), flush=True)

        for coord in ("local", "orchestrator", "ledgerless"):
            rs = run(common + ["--ledger", coord, "--policy", "MAJORITY"])
            row = {"coordinator": coord, "policy": "MAJORITY", "n": len(rs),
                   "latency_p50_s": st.median(r["latency_ms"] for r in rs) / 1000,
                   "latency_p95_s": sorted(r["latency_ms"] for r in rs)[int(0.95 * len(rs)) - 1] / 1000,
                   "writes_mean": st.mean(r["tx_writes"] for r in rs),
                   "accuracy": st.mean(r["correct"] for r in rs)}
            summary.append(row)
            print(json.dumps(row), flush=True)
    finally:
        for p in procs:
            p.terminate()
    with open(os.path.join(OUT, "summary.json"), "w") as f:
        json.dump({"stamp": stamp, "sample": a.sample, "model": MODEL, "rows": summary}, f, indent=2)
    print("summary ->", os.path.join(OUT, "summary.json"))


if __name__ == "__main__":
    main()
