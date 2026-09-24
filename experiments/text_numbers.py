#!/usr/bin/env python3
"""Every number quoted in the paper's text, recomputed from results/full.

Figures and tables come from figures.py; this script covers the numbers in
the prose (abstract, results, conclusion), so they can be checked against the
data after any rerun.

    ../.venv/bin/python text_numbers.py
"""
import glob
import itertools
import json
import os
import statistics as st

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FULL = os.path.join(ROOT, "results", "full")
ORGS = ["Org1MSP", "Org2MSP", "Org3MSP"]
POLICIES = ["ANY", "MAJORITY", "ROLE_WEIGHTED", "UNANIMOUS"]
PANELS = ["claude-sonnet-5", "gpt-6-sol", "gemini-3.8-flash", "deepseek-v4.1-flash", "llama-4-maverick", "mixed"]
SINGLE = ("prompt-poison", "confident-wrong", "sycophancy")


def load(pattern):
    return [json.loads(l) for f in sorted(glob.glob(os.path.join(FULL, pattern))) for l in open(f)]


def pct(x):
    return f"{100 * x:.1f}%"


def phi(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    va, vb = ma * (1 - ma), mb * (1 - mb)
    return None if va == 0 or vb == 0 else (sum(x * y for x, y in zip(a, b)) / n - ma * mb) / (va * vb) ** 0.5


def agents(domain):
    print(f"\n## {domain}: agents without faults (MAJORITY, no fault)")
    for p in PANELS:
        rs = load(f"{domain}/{p}/MAJORITY__none__0.00__s1__local__atk0.0.jsonl")
        ok = [[r["verdicts"][o] == r["ground_truth"] for r in rs] for o in ORGS]
        corr = [c for c in (phi([1 - x for x in a], [1 - x for x in b]) for a, b in itertools.combinations(ok, 2)) if c is not None]
        approve = st.mean(r["verdicts"][o] == "APPROVE" for r in rs for o in ORGS)
        print(f"  {p:22s} agent {pct(st.mean(sum(v) / len(v) for v in ok))}  corr {st.mean(corr):.2f}  "
              f"MAJ {pct(st.mean(r['correct'] for r in rs))}  approve {pct(approve)}")
    rs = load(f"{domain}/mixed/MAJORITY__none__0.00__s1__local__atk0.0.jsonl")
    w = [r for r in rs if r["verdicts"]["Org3MSP"] != r["ground_truth"]]
    rt = [r for r in rs if r["verdicts"]["Org3MSP"] == r["ground_truth"]]
    print(f"  mixed: majority corrects Org3 (Sonnet) in {sum(r['correct'] for r in w)}/{len(w)} it gets wrong, "
          f"outvotes it in {sum(not r['correct'] for r in rt)}/{len(rt)} it gets right")


def policies(domain):
    print(f"\n## {domain}: policies (all panels pooled)")
    for pol in POLICIES:
        clean = load(f"{domain}/*/{pol}__none__0.00__s1__local__atk0.0.jsonl")
        faulty = [r for m in SINGLE for r in load(f"{domain}/*/{pol}__{m}__*__local__atk0.0.jsonl") if r["poisoned_orgs"]]
        print(f"  {pol:14s} single fault: wrong {pct(st.mean(r['wrong_commit'] for r in faulty))} "
              f"right {pct(st.mean(r['correct'] for r in faulty))} nothing {pct(st.mean(r['committed'] is None for r in faulty))} "
              f"rounds {st.mean(r['rounds'] for r in faulty):.2f} (n={len(faulty)}) | "
              f"clean acc {pct(st.mean(r['correct'] for r in clean))} abandon {pct(st.mean(r['status'] != 'COMMITTED' for r in clean))}")
    # Rounds as in the cost figure: every case of the single fault runs at rate 0.50.
    rounds = {pol: st.mean(r["rounds"] for m in SINGLE
                           for r in load(f"{domain}/*/{pol}__{m}__0.50__*__local__atk0.0.jsonl")) for pol in POLICIES}
    print("  rounds per case, single fault runs at rate 0.50 (all cases): "
          + "  ".join(f"{pol} {v:.2f} ({100 * (v / rounds['ANY'] - 1):+.0f}%)" for pol, v in rounds.items()))
    for m in SINGLE + ("collusion",):
        row = []
        for pol in POLICIES:
            fl = [r for r in load(f"{domain}/*/{pol}__{m}__*__local__atk0.0.jsonl") if r["poisoned_orgs"]]
            row.append(f"{pol} {pct(st.mean(r['wrong_commit'] for r in fl))}")
        print(f"  {m:16s} " + "  ".join(row))
    for pol in ("MAJORITY", "ROLE_WEIGHTED", "UNANIMOUS", "ANY"):
        fl = [r for r in load(f"{domain}/*/{pol}__collusion__*__local__atk0.0.jsonl") if r["poisoned_orgs"]]
        w = [r for r in fl if "Org3MSP" in r["poisoned_orgs"]]
        wo = [r for r in fl if "Org3MSP" not in r["poisoned_orgs"]]
        print(f"  collusion {pol:14s} with mandatory {pct(st.mean(r['wrong_commit'] for r in w))}  "
              f"without {pct(st.mean(r['wrong_commit'] for r in wo))}")


def coverage(domain):
    """Risk against coverage, and wrong commits in excess of the same case
    without an injected fault (paired by panel and case)."""
    print(f"\n## {domain}: coverage and risk, single fault (all rates, panels, seeds)")
    base = {}
    for pol in POLICIES:
        for p in PANELS:
            for r in load(f"{domain}/{p}/{pol}__none__0.00__s1__local__atk0.0.jsonl"):
                base[(pol, p, r["case_id"])] = r
    faulty = {pol: [] for pol in POLICIES}
    for pol in POLICIES:
        for m in SINGLE:
            for f in sorted(glob.glob(os.path.join(FULL, domain, "*", f"{pol}__{m}__*__local__atk0.0.jsonl"))):
                p = f.split(os.sep)[-2]
                faulty[pol] += [(p, r) for r in map(json.loads, open(f)) if r["poisoned_orgs"]]
    for pol in POLICIES:
        rs = [r for _, r in faulty[pol]]
        committed = [r for r in rs if r["committed"] is not None]
        excess = st.mean(r["wrong_commit"] - base[(pol, p, r["case_id"])]["wrong_commit"] for p, r in faulty[pol])
        print(f"  {pol:14s} coverage {pct(len(committed) / len(rs))}  wrong among committed "
              f"{pct(st.mean(r['wrong_commit'] for r in committed))}  wrong over all faulty "
              f"{pct(st.mean(r['wrong_commit'] for r in rs))}  excess over no fault {100 * excess:+.1f} points")
    # Matched coverage: MAJORITY on exactly the faulty cases UNANIMOUS commits.
    un = {(p, r["case_id"], r["poisoned_orgs"][0]): r for p, r in faulty["UNANIMOUS"]}
    mj = {(p, r["case_id"], r["poisoned_orgs"][0]): r for p, r in faulty["MAJORITY"]}
    keys = [k for k, r in un.items() if r["committed"] is not None and k in mj]
    print(f"  matched coverage ({len(keys)} faulty cases UNANIMOUS commits): wrong among them "
          f"UNANIMOUS {pct(st.mean(un[k]['wrong_commit'] for k in keys))} vs MAJORITY {pct(st.mean(mj[k]['wrong_commit'] for k in keys))}")


def resistance(domain):
    print(f"\n## {domain}: poisoned agent still correct (MAJORITY, rate 0.50)")
    for p in PANELS:
        out = []
        for m in ("prompt-poison", "confident-wrong"):
            v = [r["verdicts"][o] == r["ground_truth"] for r in load(f"{domain}/{p}/MAJORITY__{m}__0.50__*__local__atk0.0.jsonl")
                 for o in r["poisoned_orgs"]]
            out.append(f"{m} {pct(st.mean(v))}")
        print(f"  {p:22s} " + "  ".join(out))


def attacks(domain):
    print(f"\n## {domain}: coordinator attacks (MAJORITY, half the cases)")
    for suffix, name in (("atk0.5", "forgery"), ("omit0.5", "omission")):
        for c in ("orchestrator", "ledgerless", "local"):
            tried = acc = changed = wrong = lost = 0
            kinds = __import__("collections").Counter()
            for p in PANELS:
                base = {r["case_id"]: r for r in load(f"{domain}/{p}/MAJORITY__none__0.00__s1__local__atk0.0.jsonl")}
                for r in load(f"{domain}/{p}/MAJORITY__none__0.00__s*__{c}__{suffix}.jsonl"):
                    if not r["attack_attempted"]:
                        continue
                    b = base[r["case_id"]]
                    d = r["committed"] != b["committed"]
                    tried += 1; acc += r["attack_succeeded"]; changed += d
                    wrong += d and r["wrong_commit"]; lost += d and b["correct"] and not r["correct"]
                    if d:
                        kinds[("right" if b["correct"] else "wrong" if b["committed"] else "none") + "->"
                              + ("right" if r["correct"] else "wrong" if r["committed"] else "none")] += 1
            if tried:
                print(f"  {name:9s} {c:13s} accepted {acc}/{tried}  changed {changed} ({pct(changed / tried)})  "
                      f"now wrong {wrong}  right lost {lost}  {dict(kinds)}")


if __name__ == "__main__":
    for d in ("contract", "planning"):
        agents(d)
        policies(d)
        coverage(d)
    resistance("contract")
    attacks("contract")
