#!/usr/bin/env python3
"""What the ledger adds over signed votes broadcast to every participant.

Three properties are separated, for four coordinators on the stored contract
verdicts (every panel, no injected faults, one round):

  forgery     can the operator commit a vote in another organisation's name?
  omission    can the operator drop a vote without the others noticing?
  agreement   can a faulty voter make honest organisations reach different
              decisions by sending them different signed votes (equivocation)?

Broadcast is the stronger baseline suggested by review: every vote is signed
and sent to every organisation, each keeps its own receipts and applies the
policy to its own copy, and there is no agreement protocol. Delivery is
reliable and every honest organisation waits for all three votes before it
applies the policy. The equivocator is one voter that is not the proposer
(Org2 or Org3); it knows the honest verdicts and, on every proposal, sends an
endorsement to one honest organisation and a rejection to the other whenever
that makes them disagree. Honest organisations vote their own fixed verdict on
every proposal they receive (Algorithm 1). A split is counted in two ways:

  round 1     after the first proposal, one honest organisation has committed
              it and the other has not (commit against not yet committed);
  protocol    after the full two round protocol, where an organisation whose
              first proposal failed proposes the other action: either the two
              honest organisations committed opposite actions (conflict), or
              one committed and the other abandoned the case (commit against
              abandon). The equivocator prefers a conflict when one is possible.

Denominator: every stored contract case of every panel without faults
(6 panels x 200 cases). Under CGC the result is derived, not simulated: each
organisation's vote is one ordered transaction, the chaincode refuses a second
vote from the same MSP identity (TestDoubleVoteFails), so every organisation
reads the same votes and the same outcome.

    ../.venv/bin/python agreement.py      # writes results/agreement.json
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "agents"))

from ledger import COMMITTED, PolicyConfig, Proposal, evaluate  # noqa: E402

PANELS = ["claude-sonnet-5", "gpt-6-sol", "gemini-3.8-flash", "deepseek-v4.1-flash", "llama-4-maverick", "mixed"]
POLICIES = ["ANY", "MAJORITY", "ROLE_WEIGHTED", "UNANIMOUS"]
ORGS = ["Org1MSP", "Org2MSP", "Org3MSP"]
EQUIVOCATOR, HONEST = "Org2MSP", ("Org1MSP", "Org3MSP")


def outcome(policy, action, votes):
    p = Proposal("c", "decide", "Org1MSP", action)
    p.votes = {o: {"endorse": e, "reason": "", "weight": 1} for o, e in votes.items()}
    return evaluate(p, PolicyConfig(policy, 3, "Org3MSP", 2, {})) == COMMITTED


def tally(policy, action, honest, eq, eq_vote):
    """Outcome at one honest organisation that received eq_vote from eq."""
    return outcome(policy, action, {**honest, eq: eq_vote})


def run_case(policy, v, eq):
    """(round-1 split, protocol conflict, protocol commit-vs-abandon) for one case."""
    hs = tuple(o for o in ORGS if o != eq)
    best = (False, False, False)
    x = v["Org1MSP"]  # Org1 proposes its own verdict
    for e1 in (True, False):  # round 1: hs[0] receives e1, hs[1] receives not e1
        views1 = {h: (e1 if h == hs[0] else not e1) for h in hs}
        honest1 = {o: v[o] == x for o in hs}
        c1 = {h: tally(policy, x, honest1, eq, views1[h]) for h in hs}
        split1 = len(set(c1.values())) > 1
        # round 2: the other action, proposed by any organisation still open
        honest2 = {o: v[o] != x for o in hs}
        for e2 in (True, False):
            views2 = {h: (e2 if h == hs[0] else not e2) for h in hs}
            final = {h: x if c1[h] else ("other" if tally(policy, not x, honest2, eq, views2[h]) else None)
                     for h in hs}
            a, b = final.values()
            conflict = a is not None and b is not None and a != b
            abandon = (a is None) != (b is None)
            best = max(best, (split1, conflict, abandon), key=lambda t: (t[1], t[2], t[0]))
            best = (best[0] or split1, best[1], best[2])
    return best


def main():
    out = {}
    cases_by_panel = {}
    for panel in PANELS:
        path = os.path.join(ROOT, "results", "full", "contract", panel,
                            "MAJORITY__none__0.00__s1__local__atk0.0.jsonl")
        cases_by_panel[panel] = [json.loads(line)["verdicts"] for line in open(path)]
    for policy in POLICIES:
        out[policy] = {}
        for eq in ("Org2MSP", "Org3MSP"):
            n = r1 = conf = aband = 0
            for panel, cases in cases_by_panel.items():
                for v in cases:
                    s1, c, a = run_case(policy, v, eq)
                    n += 1
                    r1 += s1
                    conf += c
                    aband += a
            out[policy][eq] = {"cases": n, "round1_split": r1, "conflict": conf,
                               "commit_vs_abandon": aband, "ledger_split": 0}
            print(f"{policy:14s} {eq}: round 1 split {100 * r1 / n:5.1f}%   full protocol: "
                  f"opposite commits {100 * conf / n:5.1f}%, commit vs abandon {100 * aband / n:5.1f}%  (n={n})")
        # backwards compatible keys (Org2 equivocating, round 1)
        out[policy]["cases"] = out[policy]["Org2MSP"]["cases"]
        out[policy]["broadcast_split"] = out[policy]["Org2MSP"]["round1_split"]
        out[policy]["ledger_split"] = 0
    out["properties"] = {
        "trusted orchestrator": {"forgery": "succeeds", "omission": "undetected", "agreement": "one tally, trusted"},
        "signed voting, one tally": {"forgery": "refused", "omission": "undetected", "agreement": "one tally, trusted"},
        "signed broadcast": {"forgery": "refused", "omission": "detected by the other parties",
                             "agreement": "lost under equivocation without an agreement protocol"},
        "ledger (CGC)": {"forgery": "refused", "omission": "a committed vote cannot be removed; commit can be delayed",
                         "agreement": "one ordered vote per organisation"},
    }
    json.dump(out, open(os.path.join(ROOT, "results", "agreement.json"), "w"), indent=2)


if __name__ == "__main__":
    main()
