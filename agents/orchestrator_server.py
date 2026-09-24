#!/usr/bin/env python3
"""The trusted orchestrator as a network service, for a fair latency baseline.

Serves the same JSON routes as the organisation gateways (setPolicy,
proposeStep, voteStep, finalizeStep, reproposeStep, getProposal, caseTrail,
register) but tallies votes in memory with TrustedOrchestrator. Deployed in
the same cluster and reached through a port forward like the gateways, it
measures what an orchestrator costs once network hops are included.

    python orchestrator_server.py [--port 5000]
"""
import argparse
import dataclasses
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from coordinators import TrustedOrchestrator

LEDGER = TrustedOrchestrator()
LOCK = threading.Lock()  # the orchestrator is one process; serialise the tally


def proposal_json(p):
    d = dataclasses.asdict(p)
    return {"caseID": d["case_id"], "stepID": d["step_id"], "proposer": d["proposer"],
            "actionType": d["action_type"], "status": d["status"], "outcome": d["outcome"],
            "round": d["round"], "policy": d["policy"], "votes": d["votes"]}


def handle(path, org, data):
    if path == "/register":
        return {"registered": True}
    if path == "/setPolicy":
        LEDGER.set_policy(data["policy"], int(data["totalOrgs"]), data.get("mandatoryMSP", ""),
                          int(data.get("requiredWeight", 0)), data.get("weights") or {})
        return {"ok": True}
    if path == "/proposeStep":
        LEDGER.propose_step(org, data["caseID"], data["stepID"], data["actionType"], data.get("payloadHash", ""))
        return {"ok": True}
    if path == "/voteStep":
        LEDGER.vote_step(org, data["caseID"], data["stepID"], bool(data["endorse"]), data.get("reason", ""))
        return {"ok": True}
    if path == "/finalizeStep":
        return LEDGER.finalize_step(data["caseID"], data["stepID"])
    if path == "/reproposeStep":
        LEDGER.repropose_step(org, data["caseID"], data["stepID"], data["actionType"], data.get("payloadHash", ""))
        return {"ok": True}
    if path == "/getProposal":
        return proposal_json(LEDGER.get_proposal(data["caseID"], data["stepID"]))
    if path == "/caseTrail":
        return [proposal_json(p) for k, p in LEDGER.proposals.items() if p.case_id == data["caseID"]]
    raise KeyError(path)


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _serve(self, org, data):
        try:
            with LOCK:
                self._reply(200, handle(urlparse(self.path).path, org, data))
        except Exception as e:  # mirror the gateways: errors are HTTP 500 with a message
            self._reply(500, {"error": str(e)})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        self._serve(body.get("org"), body.get("data", body))

    def do_GET(self):
        q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        data = {k[5:-1]: v for k, v in q.items() if k.startswith("data[")}
        self._serve(q.get("org"), data)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5000)
    a = ap.parse_args()
    ThreadingHTTPServer(("0.0.0.0", a.port), Handler).serve_forever()
