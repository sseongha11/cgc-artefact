# CGC dataset harvester — PINS planning-appeal decision letters → labelled JSONL

Builds the real, open-licensed ground-truth corpus for consensus gated
agents: UK Planning Inspectorate appeal decisions, each with a native binary
outcome (`allowed`→`APPROVE` / `dismissed`→`REJECT`).

## Why "letter-first"

The casework **database** (`opendatacommunities.org` SPARQL) gives labels +
metadata but (a) has **no field linking to the decision letter** and (b) returns
**HTTP 403** to non-browser clients. The decision **letter**, by contrast, is
*self-labelling* — it contains the appeal ref, outcome, procedure, cited policies,
and case facts as plain text. So the harvester fetches letters and lets each one
label itself; the DB is optional and only used (if at all) to pre-balance classes.

## Pipeline

```
discover fileids → fetch PDF → pdftotext -layout → extract → curate
                 → split (agent-facts | held-out verdict) → balance → JSONL
```

Every stage is validated on real letters (see the seed set below).

## Install

```bash
brew install poppler          # provides `pdftotext`  (Linux: apt install poppler-utils)
# Python 3.9+ stdlib only; `pypdf` is an optional fallback if pdftotext is absent.
```

## Use

```bash
# Fetch + parse + curate the seed set, write JSONL:
python3 harvest.py run --seed seed_fileids.txt --out dataset.jsonl

# Balance APPROVE/REJECT 50:50 (the real corpus skews ~2:1 to REJECT):
python3 harvest.py run --seed seed_fileids.txt --out balanced.jsonl --balance

# Ad-hoc ids; restrict headline corpus to householder + major planning:
python3 harvest.py run --fileids 62013348,55030080 --keep-types D,W --out d.jsonl

# Offline: re-parse already-downloaded PDFs (no network):
python3 harvest.py parse --pdf-dir ./pdf-cache --out d.jsonl
```

PDFs are cached in `--cache-dir` (default `./pdf-cache`) so re-runs are offline and
the extract is reproducible.

## Output schema (one JSON object per line)

| field | notes |
|---|---|
| `appeal_ref` | e.g. `APP/D1780/D/25/3360769` |
| `type_code` / `type` | 3rd ref segment → `D`=householder, `W`=major-planning, `C`=enforcement (excluded), `X`=lawful-dev-cert (excluded) |
| `label` | **`APPROVE`** (allowed) / **`REJECT`** (dismissed) — the ground truth |
| `lpa`, `appellant`, `application_ref`, `development`, `procedure`, `decision_date` | header metadata |
| `policies_cited` | NPPF / local `Policy XXn` codes |
| `facts` | **agent-visible**: header bullets, development, applicable policies |
| `held_out` | **NOT shown to agents**: `main_issue`, `reasons`, `conclusions`, `decision` — the ground-truth reasoning |
| `clean_binary` / `excluded` / `exclude_reason` | curation flags |

**Label-leak guard.** The verdict + Inspector reasoning live only in `held_out`;
`facts` is what the CGC agents receive. Verified: no verdict word leaks into `facts`.

**Curation rule (from real data).** Only single-verdict merits appeals (`D`/`W`)
are `clean_binary`. Enforcement (`C`), certificates (`X`), split "in part", and
multi-appeal (`Appeal A/B`) letters are auto-excluded with a reason — they have
compound outcomes that break a clean binary label.

## Discovering fileids at scale (the one manual-ish step)

The ACP document URL is `ViewDocument.aspx?fileid=<N>`; `<N>` is not derivable
from the appeal ref, so ids must be discovered. Options, best-effort first:

0. **ACP case pages** *(used for `seed_fileids_v2.txt`)*: `ViewCase.aspx?caseid=<last 7
   digits of the ref>` is public and shows case type, LPA, outcome and a direct link to
   the decision letter. `python3 harvest.py discover --seed seed_fileids_v2.txt --out
   seed_fileids_v2.txt --lo 3140000 --hi 3375000 --target 95` samples caseids (rate
   limited, one request per 1.2 s), keeps decided D/W cases with an Allowed or Dismissed
   outcome, pre balances by that outcome, and appends the fileids to the seed file.
1. **Search index** *(used for the seed set)* — query
   `site:acp.planninginspectorate.gov.uk "Appeal Decision"`, optionally biased with
   `"appeal is allowed"` vs `"appeal is dismissed"` to pre-balance classes, and
   scrape the `fileid=` params. Cheap, no auth.
2. **ACP case search** (`acp.planninginspectorate.gov.uk/casesearch.aspx`) — an
   ASP.NET postback form; scriptable with a session-aware scraper (Playwright).
   Search by LPA / date / type, follow each case to its decision document.
3. **Casework SPARQL** (`opendatacommunities.org/sparql`) — run from a **browser or
   allow-listed IP** (403 from headless). Query `Reference`, `Decision`,
   `Procedure`, `Type of Appeal`, `LPA Name` to get a labelled ref list, then
   resolve each ref to a letter via (1) or (2). SPARQL sketch:
   ```sparql
   SELECT ?ref ?decision ?procedure ?lpa WHERE {
     ?c <.../reference> ?ref ; <.../decision> ?decision ;
        <.../procedure> ?procedure ; <.../lpaName> ?lpa .
     FILTER(?decision IN ("Allowed","Dismissed"))
   } LIMIT 2000
   ```

Populate `seed_fileids.txt` (one id per line, `#` comments ok) and re-run.

## Validated seed set (2026-07)

8 real letters covering both classes and every exclusion path:
`4 clean householder (1 APPROVE / 3 REJECT)` + `4 correctly-excluded (C/X/multi)`.
Run `python3 harvest.py run --seed seed_fileids.txt --out dataset.jsonl` to reproduce.

## Licence / provenance

Source data © Crown copyright, Planning Inspectorate, under the
[Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
Redistribute derived data with attribution. The casework DB is a **5-year rolling
window** — snapshot and archive any extract with its fetch date for reproducibility.
