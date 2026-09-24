#!/usr/bin/env python3
"""Consortium size: n = 3, 5, 7, 9 organisations, from stored answers only.

Every contract case already has a clean answer from 15 agents (five models,
each in the three roles). A consortium of n organisations is a random set of
n of these agents, and the chaincode gate (ledger.evaluate) decides as in the
runtime: the first member proposes its answer, every member endorses exactly
when its answer equals the proposal, and a failed proposal is proposed once
more with the other action (Rmax = 2). Faulty members vote against the true
label, the worst case for a fault, and a faulty proposer proposes the wrong
action. ROLE WEIGHTED makes the last member mandatory and requires a
majority, which for n = 3 equals the rule used in the main experiments.

    ../.venv/bin/python scaling.py      # writes results/scaling.json
"""
import json
import os
import random
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "agents"))

from ledger import (ANY, COMMITTED, MAJORITY, OPEN, ROLE_WEIGHTED, UNANIMOUS,  # noqa: E402
                    PolicyConfig, Proposal, evaluate)

MODELS = ["claude-sonnet-5", "gpt-6-sol", "gemini-3.8-flash", "deepseek-v4.1-flash", "llama-4-maverick"]
ROLES = ["Org1MSP", "Org2MSP", "Org3MSP"]
SIZES = [3, 5, 7, 9]
CONSORTIA = 60      # random consortia per size
POLICIES = [ANY, MAJORITY, ROLE_WEIGHTED, UNANIMOUS]


def load_answers(domain="contract"):
    """answers[case][agent] = verdict, agent = (model, role); truth[case]."""
    answers, truth = {}, {}
    for m in MODELS:
        path = os.path.join(ROOT, "results", "full", domain, m, "MAJORITY__none__0.00__s1__local__atk0.0.jsonl")
        for r in map(json.loads, open(path)):
            truth[r["case_id"]] = r["ground_truth"]
            for role in ROLES:
                answers.setdefault(r["case_id"], {})[(m, role)] = r["verdicts"][role]
    return answers, truth


def decide(members, answer, truth, faulty, policy):
    """Committed action or None, with the runtime's gate and Rmax = 2."""
    n = len(members)
    names = [f"o{i}" for i in range(n)]
    cfg = PolicyConfig(policy, n, names[-1], n // 2 + 1, {})
    wrong = "REJECT" if truth == "APPROVE" else "APPROVE"
    first = wrong if 0 in faulty else answer[members[0]]
    for action in (first, "REJECT" if first == "APPROVE" else "APPROVE"):
        p = Proposal("c", "decide", names[0], action)
        for i, a in enumerate(members):
            endorse = (action == wrong) if i in faulty else (answer[a] == action)
            p.votes[names[i]] = {"endorse": endorse, "reason": "", "weight": 1}
        status = evaluate(p, cfg)
        if status == COMMITTED:
            return action
    return None


def main():
    answers, truth = load_answers()
    agents = sorted({a for per_case in answers.values() for a in per_case})
    rnd = random.Random(20260924)
    out = {}
    for n in SIZES:
        res = {pol: {"acc": [], "wrong1": [], "wrong2": [], "none2": []} for pol in POLICIES}
        for _ in range(CONSORTIA):
            members = rnd.sample(agents, n)
            for pol in POLICIES:
                acc, w1, w2, none2 = [], [], [], []
                for c, ans in answers.items():
                    t = truth[c]
                    acc.append(decide(members, ans, t, set(), pol) == t)
                    f1 = {rnd.randrange(n)}
                    w1.append(decide(members, ans, t, f1, pol) not in (t, None))
                    f2 = set(rnd.sample(range(n), 2))
                    d2 = decide(members, ans, t, f2, pol)
                    w2.append(d2 not in (t, None))
                    none2.append(d2 is None)
                for k, v in (("acc", acc), ("wrong1", w1), ("wrong2", w2), ("none2", none2)):
                    res[pol][k].append(st.mean(v))
        out[n] = {pol: {k: st.mean(v) for k, v in d.items()} for pol, d in res.items()}
        print(f"n={n}: " + "  ".join(f"{pol[:4]} acc {100 * d['acc']:.1f} w1 {100 * d['wrong1']:.1f} "
                                     f"w2 {100 * d['wrong2']:.1f} none2 {100 * d['none2']:.1f}"
                                     for pol, d in out[n].items()), flush=True)
    json.dump(out, open(os.path.join(ROOT, "results", "scaling.json"), "w"), indent=2)


if __name__ == "__main__":
    main()
