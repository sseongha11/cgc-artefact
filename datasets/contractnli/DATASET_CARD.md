# Dataset card: ContractNLI contract review corpus (balanced)

**File:** `corpus_balanced.jsonl` (git ignored; rebuild with `prepare.py`) · **Rows:** 200 · **Task:** binary contract review (`APPROVE` = the NDA satisfies the requirement, `REJECT` = the NDA contradicts it)
**Built:** 24 September 2026 · `../../.venv/bin/python prepare.py`
**Source and licence:** ContractNLI (Koreeda and Manning, Findings of EMNLP 2021), Hitachi America, Ltd., Creative Commons Attribution 4.0. https://stanfordnlp.github.io/contract-nli/

## Composition
- **Label balance:** APPROVE 100 / REJECT 100, balanced **within each hypothesis**, so the requirement text alone carries no information about the answer.
- **Hypotheses:** 4 of the 17 (nda-1, nda-7, nda-17, nda-20), 25 cases per label each.
- **Hypothesis filter:** a hypothesis is kept only if its rarer label is at least 20% of its definite annotations (kept: 0.29 to 0.47; dropped: 0.07 or less, so any cut between 0.08 and 0.29 gives the same set). Where one label is rare, balancing within the hypothesis over samples it, and the pilot found those labels noisy: every case all five models got wrong was an nda-2 Entailment (35 of 476 annotations) whose gold evidence reads as a contradiction. The rule was set after the first pilot and is reported as such.
- **Contracts:** 180 distinct NDAs from the train, dev and test splits (at most 2 cases per contract). Nothing is trained, so every split is usable.
- **Contract length:** median 1542 words, maximum 8428.
- **Left out:** `NotMentioned` pairs, so every case has a definite answer.

## Record shape
`facts` (shown to agents: `hypothesis`, full `contract` text) · `held_out` (never shown: the gold `choice` and the annotated `evidence` spans) · `label` · `ref` (`CNLI/<split>/<doc>/<hypothesis>`) · `doc_id`, `hypothesis_id`, `source_url`.

## Roles in the runtime
Org1 counsel for the Disclosing Party, Org2 counsel for the Receiving Party, Org3 the independent compliance reviewer (the mandatory organisation under ROLE_WEIGHTED).

## Citation
Yuta Koreeda and Christopher D. Manning. 2021. ContractNLI: A Dataset for Document-level Natural Language Inference for Contracts. In Findings of EMNLP 2021.
