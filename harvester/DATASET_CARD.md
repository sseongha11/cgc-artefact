# Dataset card: CGC planning appeal corpus (balanced)

**File:** `corpus_balanced.jsonl` · **Rows:** 200 · **Task:** binary consent adjudication (`APPROVE`=appeal allowed / `REJECT`=dismissed)
**Built:** 24 September 2026 · `harvest.py run --seed seed_fileids_v2.txt --keep-types D,W --balance --per-class 100`
**Source/licence:** UK Planning Inspectorate decision letters · © Crown copyright · Open Government Licence v3.0

## Composition
- **Label balance:** APPROVE 100 / REJECT 100, stratified 50:50 from a clean pool of 217 (105 APPROVE / 112 REJECT)
- **Type mix:** {'householder': 78, 'major-planning': 122}
- **Type × label:** householder/APPROVE=45, householder/REJECT=33, major-planning/APPROVE=55, major-planning/REJECT=67
- **Procedure:** {'written-reps': 195, 'hearing': 4, 'inquiry': 1}
- **Geography:** 131 distinct local planning authorities (by `APP/<code>`); no authority contributes more than 5 rows
- **Appeal year:** {'2016': 13, '2017': 11, '2018': 13, '2019': 17, '2020': 21, '2021': 24, '2022': 21, '2023': 24, '2024': 33, '2025': 23}
- **Superset of the July 2026 corpus:** all 48 rows of the earlier balanced set are included unchanged in label and fileid

## Quality (all 200 rows)
| check | result |
|---|---|
| verdict leak into agent visible `facts` | 0 |
| missing held out `reasons` | 3 |
| missing `policies_cited` | 7 |
| missing `lpa` | 0 |
| missing `development` | 17 |
| duplicate refs | 0 |
| letter label disagrees with ACP case page outcome | 0 (of 166 clean rows from case page discovery) |

- Held out reasoning: median **5229** chars (range 0–25873)
- Policies cited/case: median **3** (range 0–19)
- The 3 rows with empty `reasons` are older letters with no "Reasons" heading (APP/J3530/W/16/3155285, APP/T5150/W/16/3159699, APP/A1910/D/23/3329469); their reasoning sits in `main_issue` or `decision` inside `held_out`, so nothing leaks.
- Missing `development` rows are mostly section 73 appeals ("development of land without complying with conditions"), where the header gives no single description; the full header remains in `facts.header_bullets`.

## Record shape
`facts` (agent visible: header bullets, development, applicable policies) · `held_out` (ground truth NOT shown to agents: main_issue, reasons, conclusions, decision) · `label` · plus ref/type/lpa/appellant/procedure/date metadata.

## Provenance & reproduction
Fileids in `seed_fileids_v2.txt`: the 71 ids of `seed_fileids_batch.txt` (biased `site:acp.planninginspectorate.gov.uk` search, July 2026) followed by 190 ids found with `harvest.py discover`, which samples ACP case pages (`ViewCase.aspx?caseid=N`, caseids 3140000 to 3375000, rng seed 20260924, 538 pages probed at one request per 1.2 s) and keeps decided `D`/`W` cases with an Allowed or Dismissed outcome and a linked decision letter, capped at 3 per authority.
Funnel: **261 harvested → 217 clean binary → 200 balanced**. Curation auto excludes enforcement (`C`), lawful development certificate (`X`), split "in part", multiple appeal (`Appeal A/B`) letters and letters with no parseable Inspector verdict (e.g. Secretary of State recovered appeals).
Balancing keeps the first 100 per class in seed order, so the 17 clean rows left out are all from the new discovery batch (5 APPROVE, 12 REJECT).
Reproduce: `python3 harvest.py run --seed seed_fileids_v2.txt --keep-types D,W --out corpus_balanced.jsonl --balance --per-class 100`

**Snapshot note:** casework is a five year rolling window; these are a fixed 24 September 2026 snapshot. `pdf-cache/` archives the source letters for long term reproducibility.
