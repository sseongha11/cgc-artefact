#!/usr/bin/env python3
"""Figures and tables for the paper, straight from results/full.

Reads results/full/runs.csv (one row of metrics per run) and, for agent level
statistics, the per case files of the runs without faults. Writes vector PDFs
to paper/figures/ and LaTeX table bodies to paper/tables/, so every number in
the paper comes from the results without copying by hand. Works on partial
results: missing panels or conditions are skipped.

    ../.venv/bin/python figures.py

Design: colour follows the entity (a policy or a panel always has the same
hue), in the fixed categorical order of the validated palette; every series
also has its own marker and a direct label, so identity never rests on colour
alone (two hues sit below 3:1 contrast on white, and print may be greyscale).
"""
import csv
import glob
import itertools
import json
import os
import random
import statistics as st
from collections import defaultdict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FULL = os.path.join(ROOT, "results", "full")
FIG = os.path.join(ROOT, "paper", "figures")
TAB = os.path.join(ROOT, "paper", "tables")

# Validated categorical palette (light surface), fixed order; see the dataviz check.
SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e6e5e0"
MARKERS = ["o", "s", "^", "D", "v", "P"]

POLICIES = ["ANY", "MAJORITY", "UNANIMOUS", "ROLE_WEIGHTED"]
POLICY_LABEL = {"ANY": "ANY", "MAJORITY": "MAJORITY", "UNANIMOUS": "UNANIMOUS", "ROLE_WEIGHTED": "ROLE WEIGHTED"}
PANELS = ["claude-sonnet-5", "gpt-6-sol", "gemini-3.8-flash", "deepseek-v4.1-flash", "llama-4-maverick", "mixed"]
PANEL_LABEL = {"claude-sonnet-5": "Sonnet", "gpt-6-sol": "Sol", "gemini-3.8-flash": "Gemini",
               "deepseek-v4.1-flash": "DeepSeek", "llama-4-maverick": "Llama", "mixed": "Mixed"}
MODE_LABEL = {"prompt-poison": "Prompt poisoning", "confident-wrong": "Overconfidence",
              "sycophancy": "Sycophancy", "collusion": "Collusion of two agents"}
DOMAIN_LABEL = {"contract": "Contract review", "planning": "Planning appeals"}
ORGS = ["Org1MSP", "Org2MSP", "Org3MSP"]
COL = 3.33   # sigconf column width, inches
WIDE = 7.0   # figure* width

plt.rcParams.update({
    "font.family": "serif", "font.size": 7.5, "axes.labelsize": 7.5, "axes.titlesize": 7.5,
    "xtick.labelsize": 6.5, "ytick.labelsize": 6.5, "legend.fontsize": 6.5,
    "axes.edgecolor": INK2, "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2,
    "axes.spines.top": False, "axes.spines.right": False, "axes.linewidth": 0.6,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.5,
    "lines.linewidth": 1.4, "lines.markersize": 3.8, "pdf.fonttype": 42,
})


def rows():
    path = os.path.join(FULL, "runs.csv")
    if not os.path.exists(path):
        return []
    out = []
    for r in csv.DictReader(open(path)):
        for k in ("rate", "attack", "accuracy", "blast_all", "rounds_mean", "abandon",
                  "ledger_writes_mean", "cost_per_case", "commit_rate"):
            r[k] = float(r[k])
        r["blast_faulty"] = float(r["blast_faulty"]) if r["blast_faulty"] != "" else None
        for k in ("n", "n_faulty", "forgery_attempted", "forgery_succeeded", "wrong_in_forged"):
            r[k] = int(r[k])
        out.append(r)
    return out


def ci(values, reps=2000, seed=0):
    """Mean and 95% bootstrap interval."""
    values = [v for v in values if v is not None]
    if not values:
        return None, None, None
    rnd = random.Random(seed)
    means = sorted(st.mean(rnd.choices(values, k=len(values))) for _ in range(reps))
    return st.mean(values), means[int(0.025 * reps)], means[int(0.975 * reps) - 1]


def save(fig, name):
    os.makedirs(FIG, exist_ok=True)
    fig.savefig(os.path.join(FIG, name), bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    print("  figure", name)


def label_end(ax, x, y, text, color):
    ax.annotate(text, (x, y), xytext=(3, 0), textcoords="offset points",
                va="center", ha="left", fontsize=6, color=INK2)


def label_ends(ax, x, items, min_gap_frac=0.07):
    """Direct labels at the line ends, pushed apart so none overlap.
    items: list of (y, text). The gap is a fraction of the y range."""
    lo, hi = ax.get_ylim()
    gap = min_gap_frac * (hi - lo)
    placed = []
    for y, text in sorted(items):
        y_lab = max(y, placed[-1] + gap) if placed else y
        placed.append(y_lab)
        ax.annotate(text, (x, y), xytext=(x, y_lab), textcoords="data",
                    va="center", ha="left", fontsize=6, color=INK2,
                    annotation_clip=False)


# Agent level statistics from the per case files of the runs without faults.
def agent_stats(domain, panel):
    path = os.path.join(FULL, domain, panel, "MAJORITY__none__0.00__s1__local__atk0.0.jsonl")
    if not os.path.exists(path):
        return None
    rs = [json.loads(l) for l in open(path)]
    ok = [[r["verdicts"][o] == r["ground_truth"] for r in rs] for o in ORGS]
    errs = [[1 - x for x in v] for v in ok]

    def phi(a, b):
        n = len(a); ma, mb = sum(a) / n, sum(b) / n
        va, vb = ma * (1 - ma), mb * (1 - mb)
        return None if va == 0 or vb == 0 else (sum(x * y for x, y in zip(a, b)) / n - ma * mb) / (va * vb) ** 0.5

    corr = [c for c in (phi(a, b) for a, b in itertools.combinations(errs, 2)) if c is not None]
    return {
        "agent_acc": st.mean(sum(v) / len(v) for v in ok),
        "corr": st.mean(corr) if corr else float("nan"),
        "maj_acc": st.mean(r["correct"] for r in rs),
    }


def fig_agents(_rows):
    """Quorum gain over one agent against error correlation, per panel and domain."""
    domains = [d for d in DOMAIN_LABEL if os.path.isdir(os.path.join(FULL, d))]
    if not domains:
        return
    fig, axes = plt.subplots(1, len(domains), figsize=(COL, 1.55), sharey=True, squeeze=False)
    for ax, d in zip(axes[0], domains):
        for i, p in enumerate(PANELS):
            s = agent_stats(d, p)
            if not s:
                continue
            gain = 100 * (s["maj_acc"] - s["agent_acc"])
            ax.scatter(s["corr"], gain, color=SLOTS[i], marker=MARKERS[i], s=16, zorder=3,
                       edgecolors="white", linewidths=0.6)
            label_end(ax, s["corr"], gain, PANEL_LABEL[p], SLOTS[i])
        ax.axhline(0, color=INK2, linewidth=0.6)
        ax.set_title(DOMAIN_LABEL[d], color=INK)
        ax.set_xlabel("Mean error correlation")
        ax.set_xlim(0, 1.15)
    axes[0][0].set_ylabel("MAJORITY minus one agent\n(accuracy points)")
    lim = max(5, max(abs(v) for ax in axes[0] for v in ax.get_ylim()))
    axes[0][0].set_ylim(-lim, lim)  # symmetric: a quorum can also lose accuracy
    save(fig, "agents.pdf")


def fig_faults(rs, domain="contract"):
    """Blast radius among faulty cases against injection rate, per policy, per mode."""
    modes = ["prompt-poison", "confident-wrong", "sycophancy", "collusion"]
    have = [m for m in modes if any(r["mode"] == m and r["domain"] == domain for r in rs)]
    if not have:
        return
    fig, axes = plt.subplots(1, len(have), figsize=(WIDE, 1.7), sharey=True, squeeze=False)
    ends = {}
    for ax, m in zip(axes[0], have):
        for i, p in enumerate(POLICIES):
            xs, ys, lo, hi = [], [], [], []
            for rate in (0.10, 0.25, 0.50):
                sel = [r["blast_faulty"] for r in rs if r["domain"] == domain and r["mode"] == m
                       and r["policy"] == p and abs(r["rate"] - rate) < 1e-9 and r["ledger"] == "local"]
                mean, a, b = ci(sel)
                if mean is None:
                    continue
                xs.append(rate); ys.append(100 * mean); lo.append(100 * a); hi.append(100 * b)
            if not xs:
                continue
            ax.fill_between(xs, lo, hi, color=SLOTS[i], alpha=0.12, linewidth=0)
            ax.plot(xs, ys, color=SLOTS[i], marker=MARKERS[i], markeredgecolor="white",
                    markeredgewidth=0.5, label=POLICY_LABEL[p])
            ends.setdefault(m, []).append((ys[-1], POLICY_LABEL[p]))
        ax.set_title(MODE_LABEL[m], color=INK)
        ax.set_xlabel("Injection rate")
        ax.set_xticks([0.10, 0.25, 0.50])
    axes[0][0].set_ylabel("Wrong commits among\nfaulty cases (%)")
    axes[0][0].set_ylim(bottom=0)  # rates start at zero so small gaps are not inflated
    last = axes[0][len(have) - 1]
    label_ends(last, 0.515, ends.get(have[-1], []))
    axes[0][0].legend(frameon=False, loc="upper left", handlelength=1.6)
    save(fig, f"faults_{domain}.pdf")


def fig_cost(rs, domain="contract"):
    """Safety against cost per policy: blast radius (single faulty agent, rate 0.5)
    against mean conflict rounds, one point per policy with a 95% interval."""
    sel_all = [r for r in rs if r["domain"] == domain and r["ledger"] == "local"
               and r["mode"] in ("prompt-poison", "confident-wrong") and abs(r["rate"] - 0.5) < 1e-9]
    if not sel_all:
        return
    fig, ax = plt.subplots(figsize=(COL, 1.8))
    for i, p in enumerate(POLICIES):
        sel = [r for r in sel_all if r["policy"] == p]
        if not sel:
            continue
        x, xa, xb = ci([r["rounds_mean"] for r in sel])
        y, ya, yb = ci([r["blast_faulty"] for r in sel])
        ax.errorbar(x, 100 * y, xerr=[[x - xa], [xb - x]], yerr=[[100 * (y - ya)], [100 * (yb - y)]],
                    color=SLOTS[i], marker=MARKERS[i], markeredgecolor="white", markeredgewidth=0.5,
                    elinewidth=0.8, capsize=0, linestyle="none")
        label_end(ax, x, 100 * y, POLICY_LABEL[p], SLOTS[i])
    ax.set_xlabel("Mean rounds per case (conflict cost)")
    ax.set_ylabel("Wrong commits among\nfaulty cases (%)")
    ax.set_ylim(bottom=0)
    save(fig, f"cost_{domain}.pdf")


def fig_scaling():
    """Accuracy without faults and wrong commits with two colluders, against
    the number of organisations (results/scaling.json from scaling.py)."""
    path = os.path.join(ROOT, "results", "scaling.json")
    if not os.path.exists(path):
        return
    data = json.load(open(path))
    sizes = sorted(int(n) for n in data)
    fig, axes = plt.subplots(1, 2, figsize=(COL, 1.6))
    fig.subplots_adjust(wspace=0.35)
    for ax, key, title in ((axes[0], "acc", "Accuracy, no faults"),
                           (axes[1], "wrong2", "Wrong, two colluders")):
        ends = []
        for i, p in enumerate(POLICIES):
            ys = [100 * data[str(n)][p][key] for n in sizes]
            ax.plot(sizes, ys, color=SLOTS[i], marker=MARKERS[i], markeredgecolor="white",
                    markeredgewidth=0.5, label=POLICY_LABEL[p])
            ends.append((ys[-1], POLICY_LABEL[p]))
        ax.set_title(title, color=INK)
        ax.set_xlabel("Organisations")
        ax.set_xticks(sizes)
        ax.set_ylim(0, 100)
        if key == "wrong2":
            label_ends(ax, sizes[-1] + 0.3, ends, min_gap_frac=0.09)
    axes[0].set_ylabel("%")
    save(fig, "scaling.pdf")


def tab_agents(_rows):
    """Table body: agent accuracy, error correlation and MAJORITY accuracy per panel."""
    os.makedirs(TAB, exist_ok=True)
    lines = []
    for p in PANELS:
        cells = []
        for d in ("contract", "planning"):
            s = agent_stats(d, p)
            cells += ([f"{100 * s['agent_acc']:.0f}", f"{s['corr']:.2f}", f"{100 * s['maj_acc']:.0f}"]
                      if s else ["", "", ""])
        lines.append(f"    {PANEL_LABEL[p]:8s} & " + " & ".join(cells) + r" \\")
    # \bottomrule lives here: after \input it would be a misplaced \noalign
    open(os.path.join(TAB, "agents.tex"), "w").write("\n".join(lines) + "\n    \\bottomrule\n")
    print("  table agents.tex")


def _attack_counts(domain, coord, suffix):
    """Attacked cases compared with the same case without the attack."""
    tried = accepted = changed = wrong = blocked = 0
    root = os.path.join(FULL, domain)
    for panel in PANELS:
        base_path = os.path.join(root, panel, "MAJORITY__none__0.00__s1__local__atk0.0.jsonl")
        if not os.path.exists(base_path):
            continue
        base = {json.loads(l)["case_id"]: json.loads(l) for l in open(base_path)}
        for seed in ("s1", "s2", "s3"):
            path = os.path.join(root, panel, f"MAJORITY__none__0.00__{seed}__{coord}__{suffix}.jsonl")
            if not os.path.exists(path):
                continue
            for r in map(json.loads, open(path)):
                if not r["attack_attempted"]:
                    continue
                b = base[r["case_id"]]
                tried += 1
                accepted += r["attack_succeeded"]
                diff = r["committed"] != b["committed"]
                changed += diff
                wrong += diff and r["wrong_commit"]
                blocked += diff and b["correct"] and not r["correct"]
    return tried, accepted, changed, wrong, blocked


def tab_forgery(_rs, domain="contract"):
    """Table body: forgery and omission attacks under MAJORITY by coordinator.
    Each attacked case is compared with the same case run without the attack, so
    the table counts decisions the attack actually changed."""
    names = {"orchestrator": r"\TO", "ledgerless": r"\LFV", "local": r"\CGC"}
    if not os.path.isdir(os.path.join(FULL, domain)):
        return
    lines = []
    for c in ("orchestrator", "ledgerless", "local"):
        ft, fa, fc, fw, _ = _attack_counts(domain, c, "atk0.5")
        ot, oa, oc, _, ob = _attack_counts(domain, c, "omit0.5")
        if ft or ot:
            lines.append(f"    {names[c]:5s} & {fa}/{ft} & {fc} & {fw} & {oa}/{ot} & {oc} & {ob} \\\\")
    os.makedirs(TAB, exist_ok=True)
    open(os.path.join(TAB, f"forgery_{domain}.tex"), "w").write("\n".join(lines) + "\n    \\bottomrule\n")
    print(f"  table forgery_{domain}.tex")


def tab_coverage(domain="contract"):
    """Table body: coverage and risk per policy with one faulty agent, plus the
    wrong commits the fault adds over the same case without it."""
    root = os.path.join(FULL, domain)
    if not os.path.isdir(root):
        return
    names = {"ANY": r"\ANY", "MAJORITY": r"\MAJ", "ROLE_WEIGHTED": r"\RW", "UNANIMOUS": r"\UNAN"}
    lines = []
    for pol in POLICIES:
        base, rs, excess = {}, [], []
        for panel in PANELS:
            for r in map(json.loads, open(os.path.join(root, panel, f"{pol}__none__0.00__s1__local__atk0.0.jsonl"))):
                base[(panel, r["case_id"])] = r
        for panel in PANELS:
            for m in ("prompt-poison", "confident-wrong", "sycophancy"):
                for f in sorted(glob.glob(os.path.join(root, panel, f"{pol}__{m}__*__local__atk0.0.jsonl"))):
                    for r in map(json.loads, open(f)):
                        if r["poisoned_orgs"]:
                            rs.append(r)
                            excess.append(r["wrong_commit"] - base[(panel, r["case_id"])]["wrong_commit"])
        committed = [r for r in rs if r["committed"] is not None]
        lines.append(f"    {names[pol]:6s} & {100 * len(committed) / len(rs):.1f} & "
                     f"{100 * st.mean(r['wrong_commit'] for r in committed):.1f} & "
                     f"{100 * st.mean(r['wrong_commit'] for r in rs):.1f} & {100 * st.mean(excess):+.1f} \\\\")
    os.makedirs(TAB, exist_ok=True)
    open(os.path.join(TAB, f"coverage_{domain}.tex"), "w").write("\n".join(lines) + "\n    \\bottomrule\n")
    print(f"  table coverage_{domain}.tex")


def tab_guarantees(domain="contract"):
    """Table body: forgery, omission and agreement per coordinator. Broadcast's
    forgery and omission cells follow from signatures and retained receipts and
    are marked with a dagger; the other numbers are measured."""
    if not os.path.isdir(os.path.join(FULL, domain)):
        return
    agree = json.load(open(os.path.join(ROOT, "results", "agreement.json")))
    # Org2 equivocating, full two round protocol: opposite commits and commit
    # against abandon, each as MAJORITY / UNANIMOUS
    pct = lambda pol, k: 100 * agree[pol]["Org2MSP"][k] / agree[pol]["Org2MSP"]["cases"]
    fmt = lambda x: f"{x:.1f}" if x else "0"
    eq = lambda k: f"{fmt(pct('MAJORITY', k))} / {fmt(pct('UNANIMOUS', k))}"
    ft, fa, fc, _, _ = _attack_counts(domain, "orchestrator", "atk0.5")
    lt, la, _, _, _ = _attack_counts(domain, "ledgerless", "atk0.5")
    ct, ca, _, _, _ = _attack_counts(domain, "local", "atk0.5")
    ot, oa, oc, _, _ = _attack_counts(domain, "orchestrator", "omit0.5")
    lot, loa, loc, _, _ = _attack_counts(domain, "ledgerless", "omit0.5")
    cot, coa, _, _, _ = _attack_counts(domain, "local", "omit0.5")
    rows = [
        (r"\TO", f"{fa}/{ft}", f"{fc}", f"{oa}/{ot}", f"{oc}", r"\multicolumn{2}{c}{one tally}"),
        (r"\LFV", f"{la}/{lt}", "0", f"{loa}/{lot}", f"{loc}", r"\multicolumn{2}{c}{one tally}"),
        (r"\BC", r"0\textsuperscript{\dag}", r"0\textsuperscript{\dag}", r"seen\textsuperscript{\dag}",
         r"0\textsuperscript{\dag}", eq("conflict"), eq("commit_vs_abandon")),
        (r"\CGC", f"{ca}/{ct}", "0", f"{coa}/{cot}", "0", r"0\textsuperscript{\ddag}", r"0\textsuperscript{\ddag}"),
    ]
    lines = [f"    {' & '.join(r)} \\\\" for r in rows]
    open(os.path.join(TAB, f"guarantees_{domain}.tex"), "w").write("\n".join(lines) + "\n    \\bottomrule\n")
    print(f"  table guarantees_{domain}.tex")


def tab_live():
    """Table body: live Fabric cost per policy (results/live/summary.json)."""
    path = os.path.join(ROOT, "results", "live", "summary.json")
    if not os.path.exists(path):
        return
    names = {"ANY": r"\ANY", "MAJORITY": r"\MAJ", "UNANIMOUS": r"\UNAN", "ROLE_WEIGHTED": r"\RW"}
    lines = []
    for r in json.load(open(path))["rows"]:
        if r.get("coordinator") != "CGC (Fabric)" or "latency_p50_s" not in r:
            continue
        lines.append(f"    {names[r['policy']]:6s} & {r['latency_p50_s']:.1f} / {r['latency_p95_s']:.1f} & "
                     f"{r['rounds_mean']:.2f} & {r['writes_mean']:.1f} & {r['trail_bytes_mean'] / 1024:.1f} & "
                     f"{r['audit_complete']} \\\\")
    os.makedirs(TAB, exist_ok=True)
    open(os.path.join(TAB, "live.tex"), "w").write("\n".join(lines) + "\n    \\bottomrule\n")
    print("  table live.tex")


def main():
    rs = rows()
    print(f"{len(rs)} runs in runs.csv")
    fig_agents(rs)
    for d in ("contract", "planning"):
        fig_faults(rs, d)
        fig_cost(rs, d)
        tab_forgery(rs, d)
    tab_agents(rs)
    tab_live()
    tab_coverage("contract")
    tab_guarantees("contract")
    fig_scaling()


if __name__ == "__main__":
    main()
