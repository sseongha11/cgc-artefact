#!/usr/bin/env python3
"""Summarise results/pilot/*.jsonl: per panel accuracy, agreement, error
correlation, poison resistance and cost. Writes results/pilot/REPORT.md."""
import glob
import itertools
import json
import os
import statistics as st
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOMAIN = sys.argv[1] if len(sys.argv) > 1 else "planning"
OUT = os.path.join(ROOT, "results", "pilot", DOMAIN)
ORGS = ["Org1MSP", "Org2MSP", "Org3MSP"]


def load(tag):
    path = os.path.join(OUT, f"{tag}.jsonl")
    return [json.loads(l) for l in open(path)] if os.path.exists(path) else None


def phi(a, b):
    """Correlation of two binary error indicators (nan if either is constant)."""
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    va, vb = ma * (1 - ma), mb * (1 - mb)
    if va == 0 or vb == 0:
        return float("nan")
    return (sum(x * y for x, y in zip(a, b)) / n - ma * mb) / (va * vb) ** 0.5


def pct(x):
    return f"{100 * x:.0f}%"


def main():
    panels = sorted({os.path.basename(p).split("__")[0] for p in glob.glob(f"{OUT}/*__*.jsonl")})
    lines = [f"# Pilot report: {DOMAIN}", "", "20 balanced cases, offline consensus gate, reasoning off unless required.", ""]

    lines += ["## Agents without faults", "",
              "| Panel | Agent acc. | APPROVE rate | All agree | Error corr. | ANY | MAJORITY | UNANIMOUS | ROLE_WEIGHTED | $ per case | Unparsed |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for p in panels:
        clean = load(f"{p}__MAJORITY__none")
        if not clean:
            continue
        verdict_ok = [[r["verdicts"][o] == r["ground_truth"] for r in clean] for o in ORGS]
        agent_acc = st.mean(sum(v) / len(v) for v in verdict_ok)
        approve = st.mean(r["verdicts"][o] == "APPROVE" for r in clean for o in ORGS)
        agree = st.mean(len(set(r["verdicts"].values())) == 1 for r in clean)
        errs = [[1 - x for x in v] for v in verdict_ok]
        corr = [phi(a, b) for a, b in itertools.combinations(errs, 2)]
        corr = [c for c in corr if c == c]
        pol = []
        for policy in ("ANY", "MAJORITY", "UNANIMOUS", "ROLE_WEIGHTED"):
            rs = load(f"{p}__{policy}__none")
            pol.append(pct(sum(r["correct"] for r in rs) / len(rs)) if rs else "n/a")
        cost = st.mean(r["cost_usd"] for r in clean)
        unparsed = sum(r["parse_failures"] for r in clean)
        lines.append(f"| {p} | {pct(agent_acc)} | {pct(approve)} | {pct(agree)} | "
                     f"{st.mean(corr):.2f} | " if corr else f"| {p} | {pct(agent_acc)} | {pct(approve)} | {pct(agree)} | n/a | ")
        lines[-1] += " | ".join(pol) + f" | {cost:.4f} | {unparsed} |"

    lines += ["", "Accuracy under a policy counts a case as correct only if the right action committed.",
              "Error corr. is the mean pairwise correlation of the three agents' error indicators.", "",
              "## One faulty agent on every case (MAJORITY)", "",
              "| Panel | Mode | Fault resisted | Blast radius | Accuracy |", "|---|---|---|---|---|"]
    for p in panels:
        for mode in ("prompt-poison", "confident-wrong"):
            rs = load(f"{p}__MAJORITY__{mode}")
            if not rs:
                continue
            resisted = st.mean(r["verdicts"][o] == r["ground_truth"] for r in rs for o in r["poisoned_orgs"])
            lines.append(f"| {p} | {mode} | {pct(resisted)} | {pct(st.mean(r['wrong_commit'] for r in rs))} | "
                         f"{pct(st.mean(r['correct'] for r in rs))} |")

    spend = 0.0
    cache = glob.glob(os.path.join(ROOT, "results", "cache", "llm", "*", "*.json"))
    for c in cache:
        spend += float((json.load(open(c)).get("usage") or {}).get("cost") or 0)
    lines += ["", f"Total spend recorded in the cache (all domains): ${spend:.2f} over {len(cache)} calls."]
    report = "\n".join(lines) + "\n"
    open(os.path.join(OUT, "REPORT.md"), "w").write(report)
    print(report)


if __name__ == "__main__":
    main()
