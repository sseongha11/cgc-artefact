#!/usr/bin/env python3
"""
CGC dataset harvester — PINS planning-appeal decision letters -> labelled JSONL.

Letter-first design (see README): every field the CGC task needs lives *in* the
decision letter, so we fetch letters, parse them, and let each letter label
itself. The casework SPARQL DB is optional (bot-blocked from headless; no
doc-link field anyway) and only helps pre-balance classes — we balance post-hoc
instead.

Pipeline:  discover fileids -> fetch PDF -> pdftotext -> extract -> curate
           -> split (agent-facts vs held-out verdict) -> balance -> JSONL

Dependencies: `pdftotext` (poppler) on PATH; Python 3.9+ stdlib. pypdf optional
fallback. No network libs beyond urllib.

Usage:
    python harvest.py run --seed seed_fileids.txt --out dataset.jsonl
    python harvest.py run --fileids 62013348,55030080 --out d.jsonl --balance
    python harvest.py parse --pdf-dir ./pdfs --out d.jsonl        # offline
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import random
import sys
import time
import urllib.request
from dataclasses import dataclass, field, asdict
from typing import Optional

VIEWDOC = "https://acp.planninginspectorate.gov.uk/ViewDocument.aspx?fileid={fileid}"
VIEWCASE = "https://acp.planninginspectorate.gov.uk/ViewCase.aspx?caseid={caseid}"
UA = "Mozilla/5.0 (research-harvester; CGC consensus-gated-agents; +academic use)"
FETCH_DELAY = 1.2  # seconds between live requests to ACP (politeness floor)
_last_fetch = 0.0

# PINS appeal-type codes (3rd segment of APP/<lpa>/<TYPE>/<yr>/<id>).
# CGC headline corpus = single-verdict merits appeals only -> D and W.
TYPE_CODES = {
    "D": ("householder", True),          # s78 householder — clean binary
    "W": ("major-planning", True),       # s78 full/major planning — clean binary
    "C": ("enforcement", False),         # compound outcomes -> exclude from headline
    "X": ("lawful-dev-cert", False),     # different legal test -> exclude
    "F": ("full-planning", True),        # treat as merits (kept, flagged)
    "H": ("advertisement", False),
    "Q": ("cil", False),
    "Z": ("enforcement-listed", False),
}
KEEP_TYPES_DEFAULT = {"D", "W", "F"}

# Section headings seen across real letters. Matched by PREFIX after canonicalising
# to a stable key, so format variants collapse correctly, e.g.
#   "Reasons" / "Reasons for the Decision" / "Reasons for the Recommendation" -> reasons
#   "Main Issue" / "Main Issues"                                              -> main issues
#   "Conclusion" / "Conclusion and Recommendation"                           -> conclusions
# Exact-match headings (whole line equals key) — MUST be exact so the letterhead
# line "Decision date: 1 Nov 2024" is NOT mistaken for the "Decision" heading.
HEADING_EXACT = {
    "decision": "decision", "formal decision": "decision",
    "inspector’s decision": "decision", "inspector's decision": "decision",
    "background": "background", "conditions": "conditions", "condition": "conditions",
}
# Prefix-match headings — line starts with the prefix (trailing text expected), so
# format variants collapse, e.g. "Reasons for the Recommendation" -> reasons.
# (key, prefixes). Order matters: first match wins.
HEADING_PREFIX = [
    ("main issues", ["main issue"]),
    ("reasons", ["reasons", "reasoning"]),
    ("preliminary matters", ["preliminary matter", "procedural matter", "appeal procedure", "application for costs", "costs application"]),
    ("planning balance", ["planning balance", "overall balance"]),
    ("other matters", ["other matter", "other consideration"]),
    ("conclusions", ["conclusion", "recommendation"]),
]
_HEADING_MAXLEN = 45
DECISION_RE = re.compile(r"[Tt]he appeal is (allowed|dismissed)(?: in part)?", re.I)
REF_RE = re.compile(r"APP/[A-Z0-9]+/([A-Z])/\d+/\d+")
REF_FULL_RE = re.compile(r"APP/[A-Z0-9]+/[A-Z]/\d+/\d+")
POLICY_RE = re.compile(
    r"National Planning Policy Framework|NPPF|\bFramework\b|"
    # "Policy CS5" / "Policies EN1" / "Policy 7" — singular or plural, lettered or bare-number code
    r"\b[Pp]olic(?:y|ies) [A-Z]{0,4}\d{1,3}[A-Z]{0,3}|\bParagraph \d+\b"
)
DATE_RE = re.compile(r"Decision date:?\s*(\d{1,2})(?:st|nd|rd|th)?\s+(\w+\s+\d{4})", re.I)  # "5th November 2020"
# "decision of X Council" / "decision made by X" (incl. National Park Authorities and
# "London Borough of X" with no "Council"); fallback for non-determination appeals,
# "The appeal is made by A against X Council."
LPA_RE = re.compile(r"decision (?:made )?(?:of|by) (?:the )?([^.•]*?(?:Council|Authority)[^.\n•]*|"
                    r"(?:Royal |London )?Borough of [^.\n•]*)", re.I)
LPA_FALLBACK_RE = re.compile(r"made by .{1,80}? against (?:the )?((?:[\w&'’-]+ ){1,6}?(?:Council|Authority)\b[^.\n•]*)")
APPELLANT_RE = re.compile(r"appeal is made by\s+(.*?)\s+against", re.I)
DEV_RE = re.compile(r"development (?:(?:proposed|permitted|sought) (?:is|was|has been)(?: originally)?(?: described as)?|is described as)(?: for)?"
                    r"\s+(.*?)(?:\.(?:\s|$)|\s•|$)", re.I)  # searched on the flattened header
APPREF_RE = re.compile(r"[Aa]pplication Ref\.?\s*(?:is\s*)?[:.]?\s*([^\s.,\n]+)")
PROC_RE = [
    (re.compile(r"Inquiry (?:held|opened)", re.I), "inquiry"),
    (re.compile(r"Hearing held", re.I), "hearing"),
    (re.compile(r"Site visit made", re.I), "written-reps"),
]


@dataclass
class Case:
    appeal_ref: Optional[str] = None
    fileid: Optional[str] = None
    source_url: Optional[str] = None
    type_code: Optional[str] = None
    type: Optional[str] = None
    lpa: Optional[str] = None
    appellant: Optional[str] = None
    application_ref: Optional[str] = None
    development: Optional[str] = None
    procedure: Optional[str] = None
    decision_date: Optional[str] = None
    label: Optional[str] = None            # APPROVE | REJECT
    raw_decision: Optional[str] = None
    policies_cited: list = field(default_factory=list)
    facts: dict = field(default_factory=dict)      # agent-visible
    held_out: dict = field(default_factory=dict)   # ground-truth, NOT shown to agents
    clean_binary: bool = False
    excluded: bool = False
    exclude_reason: Optional[str] = None


# ---------------------------------------------------------------- fetch / text
def _polite_get(url: str, timeout: int = 60) -> bytes:
    """GET with a global rate limit (>= FETCH_DELAY s between live requests)."""
    global _last_fetch
    wait = FETCH_DELAY - (time.time() - _last_fetch)
    if wait > 0:
        time.sleep(wait)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    finally:
        _last_fetch = time.time()


def fetch_pdf(fileid: str, cache_dir: str) -> Optional[str]:
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"case_{fileid}.pdf")
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return path
    url = VIEWDOC.format(fileid=fileid)
    try:
        data = _polite_get(url)
        if data[:4] != b"%PDF":
            print(f"  ! {fileid}: not a PDF (got {data[:16]!r})", file=sys.stderr)
            return None
        with open(path, "wb") as f:
            f.write(data)
        return path
    except Exception as e:  # noqa: BLE001
        print(f"  ! {fileid}: fetch failed: {e}", file=sys.stderr)
        return None


def pdf_to_text(path: str) -> Optional[str]:
    try:
        out = subprocess.run(
            ["pdftotext", "-layout", path, "-"],
            capture_output=True, timeout=90,
        )
        if out.returncode == 0 and out.stdout:
            return out.stdout.decode("utf-8", "replace")
    except FileNotFoundError:
        pass  # fall through to pypdf
    except Exception as e:  # noqa: BLE001
        print(f"  ! pdftotext failed on {path}: {e}", file=sys.stderr)
    try:
        import pypdf  # type: ignore
        reader = pypdf.PdfReader(path)
        return "\n".join((pg.extract_text() or "") for pg in reader.pages)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- parse / split
def split_sections(text: str) -> dict:
    """Split a letter into {heading: body} using the observed heading vocabulary.
    A heading is a short standalone line matching a known heading word."""
    lines = text.splitlines()
    sections: dict[str, list[str]] = {}
    current = "_preamble"
    sections[current] = []
    for ln in lines:
        stripped = ln.strip().rstrip(":")
        norm = re.sub(r"^\d+[.)]\s*", "", stripped.lower())  # drop list numbering
        key = _heading_key(norm) if len(stripped) < _HEADING_MAXLEN else None
        if key:
            current = key
            sections.setdefault(current, [])
        else:
            sections[current].append(ln)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def _heading_key(norm: str) -> Optional[str]:
    """Canonical section key for a heading line, or None if not a heading.
    Exact match first (guards 'Decision date: ...'), then prefix rules."""
    if norm in HEADING_EXACT:
        return HEADING_EXACT[norm]
    for key, prefixes in HEADING_PREFIX:
        for p in prefixes:
            if re.match(re.escape(p) + r"s?\b", norm):  # "Other Matters", "Main Issues ..."
                return key
    return None


def extract(text: str, fileid: Optional[str]) -> Case:
    c = Case(fileid=fileid)
    if fileid:
        c.source_url = VIEWDOC.format(fileid=fileid)

    m = REF_RE.search(text)
    if m:
        c.appeal_ref = m.group(0)
        c.type_code = m.group(1)
        name, _clean = TYPE_CODES.get(c.type_code, ("other", False))
        c.type = name

    # Outcome / label — first verdict inside the Decision section if we can find it.
    sections = split_sections(text)
    decision_body = sections.get("decision", "") or text
    verdicts = DECISION_RE.findall(decision_body)
    first = DECISION_RE.search(decision_body)
    if first:
        outcome = first.group(1).lower()
        c.raw_decision = _first_sentence(decision_body, first.start())
        c.label = "APPROVE" if outcome == "allowed" else "REJECT"

    # Metadata from the header bullet block (the preamble before "Decision").
    header = sections.get("_preamble", text[:1500])
    flat = re.sub(r"\s+", " ", header)  # -layout wraps "... South Gloucestershire\n Council"
    if (mm := DATE_RE.search(text)):
        c.decision_date = _norm_date(f"{mm.group(1)} {mm.group(2)}")
    if (mm := LPA_RE.search(flat) or LPA_FALLBACK_RE.search(flat)):
        c.lpa = re.sub(r"\s+", " ", mm.group(1)).strip().rstrip(".")
    if (mm := APPELLANT_RE.search(header)):
        c.appellant = mm.group(1).strip()
    if (mm := DEV_RE.search(flat)):
        c.development = re.sub(r"\s+", " ", mm.group(1)).strip()
    if (mm := APPREF_RE.search(header)):
        c.application_ref = mm.group(1).strip()
    for rx, label in PROC_RE:
        if rx.search(text):
            c.procedure = label
            break

    c.policies_cited = sorted(set(POLICY_RE.findall(text)))

    # ---- curation: is this a single, clean, binary merits case? ----
    reasons_why = []
    distinct = {v.lower() for v in verdicts}
    if c.type_code and not TYPE_CODES.get(c.type_code, ("", False))[1]:
        reasons_why.append(f"type '{c.type_code}' has compound/non-merits outcomes")
    if len(distinct) > 1 or any("in part" in v for v in verdicts):
        reasons_why.append("multiple/split verdicts in letter")
    if re.search(r"\bAppeal [AB]\b|Appeals? [A-C] and [A-C]", text):
        reasons_why.append("multi-appeal letter (Appeal A/B)")
    if re.search(r"enforcement notice is (upheld|quashed|varied|corrected)", text, re.I):
        reasons_why.append("enforcement-notice outcome present")
    if c.label is None:
        reasons_why.append("no parseable verdict")
    c.clean_binary = not reasons_why
    if reasons_why:
        c.excluded = True
        c.exclude_reason = "; ".join(reasons_why)

    # ---- split: agent-visible facts vs held-out ground-truth reasoning ----
    c.facts = {
        "header_bullets": header.strip(),
        "development": c.development,
        "applicable_policies": c.policies_cited,
    }
    c.held_out = {
        "main_issue": sections.get("main issues", ""),
        "reasons": sections.get("reasons", ""),
        "conclusions": sections.get("conclusions", ""),
        "decision": decision_body,
    }
    return c


def _first_sentence(text: str, start: int) -> str:
    tail = text[start:start + 400]
    end = tail.find(".")
    return re.sub(r"\s+", " ", tail[: end + 1] if end != -1 else tail).strip()


def _norm_date(s: str) -> str:
    import datetime
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s.strip()


# ---------------------------------------------------------------- balance / io
def balance(cases: list[Case], per_class: Optional[int]) -> list[Case]:
    """Stratify to equal APPROVE/REJECT counts (real corpus skews to REJECT)."""
    keep = [c for c in cases if c.clean_binary and c.label]
    approve = [c for c in keep if c.label == "APPROVE"]
    reject = [c for c in keep if c.label == "REJECT"]
    n = per_class or min(len(approve), len(reject))
    out = approve[:n] + reject[:n]
    return out


def load_fileids(args) -> list[str]:
    ids: list[str] = []
    if args.fileids:
        ids += [x.strip() for x in args.fileids.split(",") if x.strip()]
    if getattr(args, "seed", None) and os.path.exists(args.seed):
        with open(args.seed) as f:
            for ln in f:
                ln = ln.split("#")[0].strip()
                if ln:
                    ids.append(ln)
    # de-dup, preserve order
    seen, uniq = set(), []
    for i in ids:
        if i not in seen:
            seen.add(i); uniq.append(i)
    return uniq


def write_jsonl(cases: list[Case], path: str) -> None:
    with open(path, "w") as f:
        for c in cases:
            f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")


def print_report(cases: list[Case]) -> None:
    total = len(cases)
    clean = [c for c in cases if c.clean_binary]
    excl = [c for c in cases if c.excluded]
    appr = sum(1 for c in clean if c.label == "APPROVE")
    rej = sum(1 for c in clean if c.label == "REJECT")
    print(f"\n=== harvest report ===")
    print(f"  parsed:          {total}")
    print(f"  clean binary:    {len(clean)}  (APPROVE={appr}, REJECT={rej})")
    print(f"  excluded:        {len(excl)}")
    for c in excl:
        print(f"    - {c.appeal_ref or c.fileid}: {c.exclude_reason}")
    types = {}
    for c in clean:
        types[c.type] = types.get(c.type, 0) + 1
    print(f"  clean by type:   {types}")


# ---------------------------------------------------------------- discover
# ACP case pages (ViewCase.aspx?caseid=<last 7 digits of the ref>) are public and
# carry the case type, LPA, outcome and a direct link to the decision letter, so
# caseids (sequential) can be sampled to find decision-letter fileids. The case
# page outcome is only a pre-filter for balancing; the letter still self-labels.
_SPAN = r'id="cphMainContent_{}"[^>]*>([^<]*)'


def probe_case(caseid: int) -> Optional[dict]:
    try:
        html = _polite_get(VIEWCASE.format(caseid=caseid), timeout=30).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        print(f"  ! case {caseid}: {e}", file=sys.stderr)
        return None
    def span(name):
        m = re.search(_SPAN.format(name), html)
        return m.group(1).strip() if m else ""
    ref = REF_FULL_RE.search(span("LabelCaseReference"))
    link = re.search(r'cphMainContent_labDecisionLink".*?fileid=(\d+)', html, re.S)
    if not ref:
        return None
    return {"caseid": caseid, "ref": ref.group(0), "type_code": ref.group(0).split("/")[2],
            "lpa": span("labLPAName"), "outcome": span("labOutcome"),
            "procedure": span("labProcedure"), "fileid": link.group(1) if link else None}


def cmd_discover(args) -> int:
    """Sample caseids in [lo, hi], keep decided D/W cases with a plain
    Allowed/Dismissed outcome and a decision letter, stop at --target per class."""
    have = set(load_fileids(args))
    keep = {t.strip().upper() for t in args.keep_types.split(",")}
    rng = random.Random(args.rng_seed)
    pool = rng.sample(range(args.lo, args.hi + 1), min(args.max_probes, args.hi - args.lo + 1))
    got = {"Allowed": [], "Dismissed": []}
    per_lpa: dict[str, int] = {}
    probes = 0
    for cid in pool:
        if all(len(v) >= args.target for v in got.values()):
            break
        probes += 1
        info = probe_case(cid)
        if not info or info["type_code"] not in keep or not info["fileid"]:
            continue
        o = info["outcome"]
        if o not in got or len(got[o]) >= args.target or info["fileid"] in have:
            continue
        if per_lpa.get(info["lpa"], 0) >= args.max_per_lpa:
            continue
        per_lpa[info["lpa"]] = per_lpa.get(info["lpa"], 0) + 1
        have.add(info["fileid"])
        got[o].append(info)
        print(f"  [{o[:3].upper()}] {info['ref']}  fileid={info['fileid']}  {info['lpa']}")
    with open(args.out, "a") as f:
        f.write(f"\n# --- ACP case-page discovery: caseids {args.lo}-{args.hi}, "
                f"rng {args.rng_seed}, {probes} probes ---\n")
        for o, rows in got.items():
            for r in rows:
                f.write(f"{r['fileid']}   # {r['ref']}  {o.upper()}  ({r['lpa']})\n")
    print(f"probed {probes} caseids: Allowed={len(got['Allowed'])} "
          f"Dismissed={len(got['Dismissed'])} -> appended to {args.out}")
    return 0


# ---------------------------------------------------------------- commands
def cmd_run(args) -> int:
    ids = load_fileids(args)
    if not ids:
        print("no fileids (use --fileids or --seed)", file=sys.stderr)
        return 2
    print(f"harvesting {len(ids)} letters -> {args.out}")
    cases: list[Case] = []
    for fid in ids:
        path = fetch_pdf(fid, args.cache_dir)
        if not path:
            continue
        text = pdf_to_text(path)
        if not text:
            print(f"  ! {fid}: no text extracted", file=sys.stderr)
            continue
        c = extract(text, fid)
        cases.append(c)
        flag = "OK " if c.clean_binary else "SKIP"
        print(f"  [{flag}] {c.appeal_ref or fid}  {c.label or '-':7} {c.type or '-'}")
    if args.keep_types:
        keep = {t.strip().upper() for t in args.keep_types.split(",")}
        for c in cases:
            if c.type_code and c.type_code not in keep and c.clean_binary:
                c.clean_binary = False; c.excluded = True
                c.exclude_reason = f"type '{c.type_code}' not in keep-set {sorted(keep)}"
    out_cases = balance(cases, args.per_class) if args.balance else cases
    write_jsonl(out_cases, args.out)
    print_report(cases)
    if args.balance:
        print(f"  balanced output: {len(out_cases)} rows -> {args.out}")
    return 0


def cmd_parse(args) -> int:
    """Offline: parse already-downloaded PDFs in a directory."""
    pdfs = [os.path.join(args.pdf_dir, f) for f in sorted(os.listdir(args.pdf_dir))
            if f.lower().endswith(".pdf")]
    cases = []
    for p in pdfs:
        text = pdf_to_text(p)
        if not text:
            continue
        fid = re.search(r"(\d{5,})", os.path.basename(p))
        cases.append(extract(text, fid.group(1) if fid else None))
    write_jsonl(cases, args.out)
    print_report(cases)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="CGC PINS decision-letter harvester")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="fetch + parse + curate + write JSONL")
    r.add_argument("--fileids", help="comma-separated ACP fileids")
    r.add_argument("--seed", help="file with one fileid per line (# comments ok)")
    r.add_argument("--out", default="dataset.jsonl")
    r.add_argument("--cache-dir", default="./pdf-cache")
    r.add_argument("--keep-types", default="D,W,F", help="type codes for headline corpus")
    r.add_argument("--balance", action="store_true", help="stratify APPROVE/REJECT 50:50")
    r.add_argument("--per-class", type=int, default=None)
    r.set_defaults(func=cmd_run)

    p = sub.add_parser("parse", help="parse local PDFs (offline)")
    p.add_argument("--pdf-dir", required=True)
    p.add_argument("--out", default="dataset.jsonl")
    p.set_defaults(func=cmd_parse)

    d = sub.add_parser("discover", help="find decision-letter fileids via ACP case pages")
    d.add_argument("--lo", type=int, required=True, help="lowest caseid to sample")
    d.add_argument("--hi", type=int, required=True, help="highest caseid to sample")
    d.add_argument("--target", type=int, default=50, help="letters wanted per outcome")
    d.add_argument("--max-probes", type=int, default=500)
    d.add_argument("--max-per-lpa", type=int, default=3, help="LPA diversity cap")
    d.add_argument("--keep-types", default="D,W")
    d.add_argument("--rng-seed", type=int, default=0)
    d.add_argument("--seed", help="seed file whose fileids are skipped (usually --out itself)")
    d.add_argument("--fileids", default=None)
    d.add_argument("--out", required=True, help="seed file to append to")
    d.set_defaults(func=cmd_discover)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
