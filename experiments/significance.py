#!/usr/bin/env python3
"""Uncertainty for the policy comparisons quoted in the paper.

The same cases recur across panels, faults, rates and seeds, so observations
are not independent and a plain McNemar test would overstate confidence.
Each comparison is therefore a paired difference in rates with a 95%
interval from a bootstrap that resamples whole cases (all observations of a
case move together), plus the bootstrap share of resamples on the other side
of zero as a two sided p value.

    ../.venv/bin/python significance.py      # writes results/significance.txt
"""
import glob
import json
import os
import random
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FULL = os.path.join(ROOT, "results", "full")
SINGLE = ("prompt-poison", "confident-wrong", "sycophancy")
REPS = 2000


def rows(domain, policy, modes):
    """Observations keyed by (panel, mode, rate, seed, case) for one policy."""
    out = {}
    for mode in modes:
        pat = (f"{policy}__none__0.00__s1__local__atk0.0.jsonl" if mode == "none"
               else f"{policy}__{mode}__*__local__atk0.0.jsonl")
        for f in glob.glob(os.path.join(FULL, domain, "*", pat)):
            panel = f.split(os.sep)[-2]
            parts = os.path.basename(f).split("__")
            for r in map(json.loads, open(f)):
                if mode != "none" and not r["poisoned_orgs"]:
                    continue
                out[(panel, mode, parts[2], parts[3], r["case_id"])] = r
    return out


def compare(domain, a, b, modes, metric, label):
    ra, rb = rows(domain, a, modes), rows(domain, b, modes)
    keys = sorted(set(ra) & set(rb))
    by_case = defaultdict(list)  # case -> list of (value_a, value_b)
    for k in keys:
        by_case[k[-1]].append((metric(ra[k]), metric(rb[k])))
    cases = sorted(by_case)

    def diff(sample):
        pairs = [p for c in sample for p in by_case[c]]
        return sum(x - y for x, y in pairs) / len(pairs)

    point = diff(cases)
    rnd = random.Random(0)
    boots = sorted(diff(rnd.choices(cases, k=len(cases))) for _ in range(REPS))
    lo, hi = boots[int(0.025 * REPS)], boots[int(0.975 * REPS) - 1]
    other = sum((d <= 0) if point > 0 else (d >= 0) for d in boots) / REPS
    p = min(1.0, 2 * other)
    return (f"{domain:8s} {label:52s} {100 * point:+6.1f} points  95% CI [{100 * lo:+5.1f}, {100 * hi:+5.1f}]  "
            f"p {'< 0.001' if p < 0.001 else f'= {p:.3f}'}  ({len(keys)} pairs, {len(cases)} cases)")


def main():
    wrong = lambda r: r["wrong_commit"]
    right = lambda r: r["correct"]
    lines = []
    for d in ("contract", "planning"):
        lines += [
            compare(d, "ANY", "MAJORITY", SINGLE, wrong, "single fault, wrong commits: ANY minus MAJORITY"),
            compare(d, "MAJORITY", "ROLE_WEIGHTED", SINGLE, wrong, "single fault, wrong commits: MAJORITY minus ROLE WTD"),
            compare(d, "ROLE_WEIGHTED", "UNANIMOUS", SINGLE, wrong, "single fault, wrong commits: ROLE WTD minus UNANIMOUS"),
            compare(d, "MAJORITY", "UNANIMOUS", SINGLE, right, "single fault, right commits: MAJORITY minus UNANIMOUS"),
            compare(d, "MAJORITY", "ANY", ("collusion",), wrong, "collusion, wrong commits: MAJORITY minus ANY"),
            compare(d, "ROLE_WEIGHTED", "UNANIMOUS", ("collusion",), wrong, "collusion, wrong commits: ROLE WTD minus UNANIMOUS"),
            compare(d, "MAJORITY", "ANY", ("none",), right, "no fault, accuracy: MAJORITY minus ANY"),
            compare(d, "MAJORITY", "UNANIMOUS", ("none",), right, "no fault, accuracy: MAJORITY minus UNANIMOUS"),
        ]
    text = "\n".join(lines)
    print(text)
    open(os.path.join(ROOT, "results", "significance.txt"), "w").write(text + "\n")


if __name__ == "__main__":
    main()
