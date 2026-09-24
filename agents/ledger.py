"""Ledger backends for the CGC agent runtime.

`LocalLedger` re-implements the agentAction chaincode's endorsement gate in
Python, byte-for-byte with the Go `evaluate()`, so the harness runs offline and
the results match an on-chain run. `ApiLedger` drives the real Fabric network via
the Node API (`/proposeStep`, `/voteStep`, `/finalizeStep`, ...). Both expose the
same interface, so `run.py` is backend-agnostic.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

# policy + status constants — mirror agentAction.go
ANY, MAJORITY, UNANIMOUS, ROLE_WEIGHTED = "ANY", "MAJORITY", "UNANIMOUS", "ROLE_WEIGHTED"
OPEN, COMMITTED, REJECTED = "OPEN", "COMMITTED", "REJECTED"


class AttackRejected(Exception):
    """A coordinator refused an operator's attempt to forge or drop another
    organisation's vote."""


ForgeryRejected = AttackRejected  # earlier name, kept for callers


@dataclass
class PolicyConfig:
    policy: str = MAJORITY
    total_orgs: int = 3
    mandatory_msp: str = ""
    required_weight: int = 0
    weights: dict = field(default_factory=dict)

    def weight_of(self, msp: str) -> int:
        return self.weights.get(msp, 1)


@dataclass
class Proposal:
    case_id: str
    step_id: str
    proposer: str
    action_type: str
    status: str = OPEN
    outcome: str = ""
    round: int = 1
    policy: str = ""
    votes: dict = field(default_factory=dict)  # msp -> {"endorse":bool,"reason":str,"weight":int}


def evaluate(p: Proposal, cfg: PolicyConfig) -> str:
    """Exact port of agentAction.go evaluate(): COMMITTED | REJECTED | OPEN."""
    endorsers = endorse_weight = dissent = 0
    mandatory_endorsed = mandatory_dissented = False
    for msp, v in p.votes.items():
        if v["endorse"]:
            endorsers += 1
            endorse_weight += v["weight"]
            if msp == cfg.mandatory_msp:
                mandatory_endorsed = True
        else:
            dissent += 1
            if msp == cfg.mandatory_msp:
                mandatory_dissented = True
    n = cfg.total_orgs
    voted = len(p.votes)
    remaining = max(0, n - voted)

    if cfg.policy == ANY:
        if endorsers >= 1:
            return COMMITTED
        if remaining == 0:
            return REJECTED
    elif cfg.policy == MAJORITY:
        need = n // 2 + 1
        if endorsers >= need:
            return COMMITTED
        if endorsers + remaining < need:
            return REJECTED
    elif cfg.policy == UNANIMOUS:
        if dissent > 0:
            return REJECTED
        if endorsers >= n:
            return COMMITTED
    elif cfg.policy == ROLE_WEIGHTED:
        if mandatory_dissented:
            return REJECTED
        if mandatory_endorsed and endorse_weight >= cfg.required_weight:
            return COMMITTED
        max_reachable = endorse_weight + remaining
        if (not mandatory_endorsed and remaining == 0) or max_reachable < cfg.required_weight:
            return REJECTED
    return OPEN


class LocalLedger:
    """In-process ledger; identical gate semantics to the chaincode."""

    def __init__(self):
        self.cfg = PolicyConfig()
        self.proposals: dict[str, Proposal] = {}
        self.tx_count = 0  # proxy for on-chain write count

    def _key(self, case_id, step_id):
        return f"prop:{case_id}:{step_id}"

    def set_policy(self, policy, total_orgs, mandatory_msp="", required_weight=0, weights=None):
        self.cfg = PolicyConfig(policy, total_orgs, mandatory_msp, required_weight, weights or {})
        self.tx_count += 1

    def propose_step(self, org, case_id, step_id, action_type, payload_hash=""):
        key = self._key(case_id, step_id)
        if key in self.proposals and self.proposals[key].status == OPEN:
            raise RuntimeError("step already open")
        self.proposals[key] = Proposal(case_id, step_id, org, action_type)
        self.tx_count += 1

    def vote_step(self, org, case_id, step_id, endorse, reason=""):
        p = self.proposals[self._key(case_id, step_id)]
        if p.status != OPEN:
            raise RuntimeError(f"step is {p.status}")
        if org in p.votes:
            raise RuntimeError("already voted")
        p.votes[org] = {"endorse": endorse, "reason": reason, "weight": self.cfg.weight_of(org)}
        self.tx_count += 1

    def forge_vote(self, attacker, victim, case_id, step_id, endorse, reason=""):
        # The chaincode takes the voter from the transaction's client identity, so
        # an attacker holding only its own credentials can only ever vote as itself.
        raise ForgeryRejected(f"{attacker} cannot sign a vote as {victim}")

    def drop_vote(self, attacker, victim, case_id, step_id):
        # Each organisation submits its own vote transaction through its own
        # gateway, and the ledger is append only: no other party can remove it.
        raise AttackRejected(f"{attacker} cannot remove the vote {victim} committed to the ledger")

    def finalize_step(self, case_id, step_id) -> str:
        p = self.proposals[self._key(case_id, step_id)]
        if p.status != OPEN:
            return p.status
        decision = evaluate(p, self.cfg)
        if decision != OPEN:
            p.status = decision
            p.policy = self.cfg.policy
            if decision == COMMITTED:
                p.outcome = p.action_type
            self.tx_count += 1
        return decision

    def repropose_step(self, org, case_id, step_id, action_type, payload_hash=""):
        p = self.proposals[self._key(case_id, step_id)]
        if p.status == COMMITTED:
            raise RuntimeError("already committed")
        p.proposer, p.action_type = org, action_type
        p.round += 1
        p.status, p.outcome, p.votes = OPEN, "", {}
        self.tx_count += 1

    def get_proposal(self, case_id, step_id) -> Proposal:
        return self.proposals[self._key(case_id, step_id)]


class ApiLedger:
    """Drives the real Fabric network through the Node API (CGC routes)."""

    def __init__(self, base_url, org_users: dict, channel_org="Org1MSP", gateways: dict | None = None):
        import requests  # lazy: only needed for live runs
        self._requests = requests
        self.base = base_url.rstrip("/")
        # msp -> gateway URL. One gateway per org holds only that org's wallet, so
        # no single process can sign for every organisation.
        self.gateways = {o: u.rstrip("/") for o, u in (gateways or {}).items()}
        self.org_users = org_users  # msp -> userId registered in the wallet
        self.channel_org = channel_org
        # State-changing writes, counted exactly as LocalLedger counts them
        # (a FinalizeStep that leaves the step OPEN writes nothing).
        self.tx_count = 0

    def _url(self, org):
        return self.gateways.get(org, self.base)

    def _post(self, path, org, data):
        if path != "/finalizeStep":
            self.tx_count += 1
        r = self._requests.post(
            f"{self._url(org)}{path}",
            json={"org": org, "userId": self.org_users[org], "data": data},
            timeout=60,
        )
        r.raise_for_status()
        return r.json()

    def _get(self, path, data):
        org = self.channel_org
        params = {"org": org, "userId": self.org_users[org]}
        params.update({f"data[{k}]": v for k, v in data.items()})
        r = self._requests.get(f"{self._url(org)}{path}", params=params, timeout=60)
        r.raise_for_status()
        return r.json()

    def set_policy(self, policy, total_orgs, mandatory_msp="", required_weight=0, weights=None):
        return self._post("/setPolicy", self.channel_org, {
            "policy": policy, "totalOrgs": total_orgs, "mandatoryMSP": mandatory_msp,
            "requiredWeight": required_weight, "weights": weights or {},
        })

    def propose_step(self, org, case_id, step_id, action_type, payload_hash=""):
        return self._post("/proposeStep", org, {
            "caseID": case_id, "stepID": step_id, "actionType": action_type, "payloadHash": payload_hash})

    def vote_step(self, org, case_id, step_id, endorse, reason=""):
        return self._post("/voteStep", org, {
            "caseID": case_id, "stepID": step_id, "endorse": endorse, "reason": reason})

    def forge_vote(self, attacker, victim, case_id, step_id, endorse, reason=""):
        """The attacker asks its own gateway to cast the victim's vote."""
        r = self._requests.post(
            f"{self._url(attacker)}/voteStep",
            json={"org": victim, "userId": self.org_users[victim],
                  "data": {"caseID": case_id, "stepID": step_id, "endorse": endorse, "reason": reason}},
            timeout=60,
        )
        if not r.ok:
            raise ForgeryRejected(f"{attacker} gateway refused a vote as {victim}: HTTP {r.status_code}")
        self.tx_count += 1

    def drop_vote(self, attacker, victim, case_id, step_id):
        """The chaincode has no operation that removes a vote, and the victim
        submitted it through its own gateway, so there is nothing to drop."""
        raise AttackRejected(f"{attacker} cannot remove {victim}'s vote from the ledger")

    def finalize_step(self, case_id, step_id):
        status = self._post("/finalizeStep", self.channel_org, {"caseID": case_id, "stepID": step_id})
        if status != OPEN:
            self.tx_count += 1
        return status

    def repropose_step(self, org, case_id, step_id, action_type, payload_hash=""):
        return self._post("/reproposeStep", org, {
            "caseID": case_id, "stepID": step_id, "actionType": action_type, "payloadHash": payload_hash})

    def get_proposal(self, case_id, step_id):
        return self._get("/getProposal", {"caseID": case_id, "stepID": step_id})
