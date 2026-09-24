#!/usr/bin/env python3
"""Latency and throughput with several cases in flight, Fabric against a
networked trusted orchestrator.

Both targets are reached the same way, through port forwards into the same
cluster, and replay the Sonnet panel's stored answers, so the comparison
isolates the coordination path. For each target and each concurrency level,
48 balanced contract cases run under MAJORITY with Rmax = 2.

Needs the testbed (deploy/scripts/up.sh) and the orchestrator service
(deploy/agent-action/orchestrator.yaml).

    ../.venv/bin/python throughput.py     # writes results/live/throughput.json
"""
import json
import os
import statistics as st
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "agents"))
sys.path.insert(0, HERE)

import requests  # noqa: E402
from graph import build_workflow, run_case  # noqa: E402
from ledger import ApiLedger  # noqa: E402
from live import CORPUS, MODEL, USERS, gw, open_gateways  # noqa: E402
from reviewer import make_reviewer  # noqa: E402
from run import balanced_sample, load_corpus, load_dotenv  # noqa: E402

N = 48
LEVELS = [1, 4, 16]
ORCH_PORT = 5055  # 5000 is taken by the macOS AirPlay receiver


def open_orchestrator():
    p = subprocess.Popen(["kubectl", "-n", "cgc", "port-forward", "svc/orchestrator", f"{ORCH_PORT}:5000"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        try:
            requests.get(f"http://localhost:{ORCH_PORT}/getProposal", timeout=2)
            break
        except requests.exceptions.ConnectionError:
            time.sleep(1)
    return p


def run_level(target, conc, stamp, reviewer, users):
    if target == "fabric":
        base, gateways = gw(1), {f"Org{i}MSP": gw(i) for i in (1, 2, 3)}
    else:
        base = f"http://localhost:{ORCH_PORT}"
        gateways = {f"Org{i}MSP": base for i in (1, 2, 3)}
    cases = balanced_sample(load_corpus(CORPUS, None, f"tp-{stamp}-{target}-{conc}"), N, "sample")
    ApiLedger(base, users, gateways=gateways).set_policy("MAJORITY", 3)

    def one(case):
        app, led = build_workflow(), ApiLedger(base, users, gateways=gateways)
        return run_case(app, case, reviewer, led, {}, 2)

    t0 = time.perf_counter()
    with ThreadPoolExecutor(conc) as pool:
        rs = list(pool.map(one, cases))
    wall = time.perf_counter() - t0
    lat = sorted(r["latency_ms"] / 1000 for r in rs)
    row = {"target": target, "concurrency": conc, "cases": len(rs), "wall_s": round(wall, 2),
           "cases_per_s": round(len(rs) / wall, 3), "latency_p50_s": round(st.median(lat), 3),
           "latency_p95_s": round(lat[int(0.95 * len(lat)) - 1], 3),
           "committed": sum(r["committed"] is not None for r in rs)}
    print(json.dumps(row), flush=True)
    return row


def main():
    load_dotenv(os.path.join(ROOT, ".env"))
    reviewer = make_reviewer("openrouter", model=MODEL)
    users = dict(kv.split("=", 1) for kv in USERS.split(","))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    procs = open_gateways() + [open_orchestrator()]
    rows = []
    try:
        for target in ("orchestrator", "fabric"):
            for conc in LEVELS:
                rows.append(run_level(target, conc, stamp, reviewer, users))
    finally:
        for p in procs:
            p.terminate()
    os.makedirs(os.path.join(ROOT, "results", "live"), exist_ok=True)
    json.dump({"stamp": stamp, "cases": N, "policy": "MAJORITY", "rows": rows},
              open(os.path.join(ROOT, "results", "live", "throughput.json"), "w"), indent=2)


if __name__ == "__main__":
    main()
