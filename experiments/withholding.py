#!/usr/bin/env python3
"""Withholding endorsement on the live network: can one organisation block
the others by refusing to endorse?

Org3 withholds by stopping its chaincode server, so its peer can endorse
nothing. Org1 then proposes a case and Org1 and Org2 vote and finalise under a
MAJORITY gate. The result depends on the native endorsement policy of the
current chaincode definition: with endorsement by all three organisations the
transactions cannot commit, while with two of three they can. Org3's server is
restored afterwards.

    ../.venv/bin/python withholding.py LABEL   # appends to results/live/withholding.json
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import requests  # noqa: E402
from live import gw, open_gateways  # noqa: E402

USER = {f"Org{i}MSP": f"agent-org{i}" for i in (1, 2, 3)}


def post(org, path, data):
    i = int(org[3])
    r = requests.post(gw(i) + path, json={"org": org, "userId": USER[org], "data": data}, timeout=120)
    return r.status_code == 200, (r.text[:160] if r.status_code != 200 else r.json())


def kubectl(*args):
    subprocess.run(["kubectl", "-n", "cgc", *args], check=True, capture_output=True)


def main():
    label = sys.argv[1]
    definition = subprocess.run(
        ["kubectl", "-n", "cgc", "exec", "deploy/peer0-org1", "-c", "peer", "--", "sh", "-c",
         "CORE_PEER_MSPCONFIGPATH=/organizations/peerOrganizations/org1.example.com/users/Admin@org1.example.com/msp "
         "peer lifecycle chaincode querycommitted -C mychannel -n agentaction"],
        capture_output=True, text=True).stdout.strip().splitlines()[-1]
    procs = open_gateways()
    case = f"withhold-{label}-{int(time.time())}"
    steps = []
    try:
        ok, _ = post("Org1MSP", "/setPolicy", {"policy": "MAJORITY", "totalOrgs": 3})
        kubectl("scale", "deployment/agentaction-org3", "--replicas=0")
        kubectl("wait", "--for=delete", "pod", "-l", "app=agentaction-org3", "--timeout=120s")
        for org, path, data in (("Org1MSP", "/proposeStep", {"caseID": case, "stepID": "decide", "actionType": "APPROVE"}),
                                ("Org1MSP", "/voteStep", {"caseID": case, "stepID": "decide", "endorse": True, "reason": "w"}),
                                ("Org2MSP", "/voteStep", {"caseID": case, "stepID": "decide", "endorse": True, "reason": "w"}),
                                ("Org1MSP", "/finalizeStep", {"caseID": case, "stepID": "decide"})):
            ok, detail = post(org, path, data)
            steps.append({"org": org, "call": path, "committed": ok, "detail": detail})
            print(f"  {org} {path}: {'committed' if ok else 'failed'} {'' if ok else detail}", flush=True)
    finally:
        kubectl("scale", "deployment/agentaction-org3", "--replicas=1")
        kubectl("rollout", "status", "deployment/agentaction-org3", "--timeout=120s")
        for p in procs:
            p.terminate()
    path = os.path.join(ROOT, "results", "live", "withholding.json")
    runs = json.load(open(path)) if os.path.exists(path) else []
    runs.append({"label": label, "definition": definition, "withholding": "Org3", "gate": "MAJORITY", "steps": steps})
    json.dump(runs, open(path, "w"), indent=2)


if __name__ == "__main__":
    main()
