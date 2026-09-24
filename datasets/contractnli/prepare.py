#!/usr/bin/env python3
"""Build a balanced binary corpus from ContractNLI (Koreeda and Manning, 2021).

Source: https://stanfordnlp.github.io/contract-nli/ (CC BY 4.0). The zip is
downloaded into raw/ (git ignored). All three splits are used: nothing is
trained, and the test split alone has too few contradictions to balance.

A case pairs one NDA with one of the 17 fixed hypotheses. Label:
  Entailment    -> APPROVE (the agreement satisfies the requirement)
  Contradiction -> REJECT  (the agreement contradicts it)
NotMentioned pairs are left out, so every case has a definite answer.

Labels are balanced *within each hypothesis*: sampling takes one APPROVE and
one REJECT case per hypothesis per round, round robin, so the requirement text
alone carries no information about the answer. (Balancing only overall left
nda-2 as REJECT 43 times and nda-13 only as APPROVE: a shortcut.) Sampling is
seeded and capped at CAP cases per contract.

Only hypotheses whose rarer label is at least MIN_MINORITY of their definite
annotations are used. Where one label is rare, balancing within the
hypothesis over samples it, and the pilot found those rare labels noisy:
every case all five models got wrong was an nda-2 Entailment (35 of 476
annotations) whose gold evidence reads as a contradiction.

    ../../.venv/bin/python prepare.py            # writes corpus_balanced.jsonl
"""
import argparse
import collections
import hashlib
import json
import os
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
URL = "https://stanfordnlp.github.io/contract-nli/resources/contract-nli.zip"
RAW = os.path.join(HERE, "raw")
LABEL = {"Entailment": "APPROVE", "Contradiction": "REJECT"}


def fetch(split: str) -> str:
    path = os.path.join(RAW, "contract-nli", f"{split}.json")
    if not os.path.exists(path):
        os.makedirs(RAW, exist_ok=True)
        z = os.path.join(RAW, "contract-nli.zip")
        urllib.request.urlretrieve(URL, z)
        zipfile.ZipFile(z).extractall(RAW)
    return path


def rank(seed: str, key: str) -> str:
    return hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-label", type=int, default=100)
    ap.add_argument("--cap", type=int, default=2, help="max cases per contract")
    ap.add_argument("--seed", default="contractnli")
    ap.add_argument("--min-minority", type=float, default=0.2,
                    help="keep hypotheses whose rarer label is at least this share")
    ap.add_argument("--out", default=os.path.join(HERE, "corpus_balanced.jsonl"))
    args = ap.parse_args()

    pool = []
    for split in ("train", "dev", "test"):
        data = json.load(open(fetch(split)))
        hyps = data["labels"]
        for doc in data["documents"]:
            spans = doc["spans"]
            for hid, ann in doc["annotation_sets"][0]["annotations"].items():
                if ann["choice"] not in LABEL:
                    continue
                pool.append({
                    "domain": "contract",
                    "ref": f"CNLI/{split}/{doc['id']}/{hid}",
                    "label": LABEL[ann["choice"]],
                    "clean_binary": True,
                    "doc_id": f"{split}/{doc['id']}",
                    "hypothesis_id": hid,
                    "source_url": doc.get("url", ""),
                    "facts": {"hypothesis": hyps[hid]["hypothesis"], "contract": doc["text"]},
                    "held_out": {
                        "choice": ann["choice"],
                        "evidence": [doc["text"][spans[i][0]:spans[i][1]] for i in ann["spans"]],
                    },
                })

    # queue[h][label]: that hypothesis's cases of that label, in seeded order
    queue = collections.defaultdict(lambda: collections.defaultdict(list))
    for c in sorted(pool, key=lambda c: rank(args.seed, c["ref"])):
        queue[c["hypothesis_id"]][c["label"]].append(c)
    def minority(h):
        a, r = len(queue[h]["APPROVE"]), len(queue[h]["REJECT"])
        return min(a, r) / (a + r)
    both = sorted(h for h in queue if minority(h) >= args.min_minority)
    print("hypotheses kept:", {h: round(minority(h), 2) for h in both})
    print("hypotheses dropped:", {h: round(minority(h), 2) for h in sorted(set(queue) - set(both))})

    per_doc = collections.Counter()

    def take(h, label):
        while queue[h][label]:
            c = queue[h][label].pop(0)
            if per_doc[c["doc_id"]] < args.cap:
                per_doc[c["doc_id"]] += 1
                return c
        return None

    out = []
    while len(out) < 2 * args.per_label:
        progressed = False
        for h in both:
            if len(out) >= 2 * args.per_label:
                break
            a, r = take(h, "APPROVE"), take(h, "REJECT")
            if a and r:
                out += [a, r]
                progressed = True
            else:  # keep the hypothesis balanced: return an unmatched pick
                for c in (a, r):
                    if c:
                        per_doc[c["doc_id"]] -= 1
        if not progressed:
            break
    out.sort(key=lambda c: rank(args.seed, c["ref"]))
    with open(args.out, "w") as f:
        for c in out:
            f.write(json.dumps(c) + "\n")

    words = sorted(len(c["facts"]["contract"].split()) for c in out)
    print(f"pool: {collections.Counter(c['label'] for c in pool)}")
    print(f"wrote {len(out)} cases -> {args.out}")
    print(f"labels: {collections.Counter(c['label'] for c in out)}")
    print(f"contracts: {len({c['doc_id'] for c in out})}, hypotheses: {len({c['hypothesis_id'] for c in out})}")
    print(f"contract words: median {words[len(words)//2]}, max {words[-1]}")
    print(f"hypotheses x label: {sorted(collections.Counter((c['hypothesis_id'], c['label']) for c in out).items())}")


if __name__ == "__main__":
    main()
