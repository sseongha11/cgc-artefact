# CGC agent runtime — consensus-gated multi-agent workflow (LangGraph)

Runs the experiment of the paper: three org-agents independently adjudicate a real
planning appeal, and a configurable **endorsement policy** — enforced by the
`agentAction` chaincode — decides whether their action commits. Measures
accuracy, **blast radius** of a hallucination, conflict-resolution rounds, latency,
and on-chain writes.

## Pieces

| file | role |
|---|---|
| `graph.py` | LangGraph workflow: 3 parallel `review_*` nodes → `propose` → `vote` → `finalize`, with a bounded `repropose` loop |
| `reviewer.py` | `ClaudeReviewer` (real, reads only agent-visible `facts`) · `MockReviewer` (deterministic simulation with tunable accuracy, for offline runs) |
| `ledger.py` | `LocalLedger` (Python port of the chaincode gate — offline) · `ApiLedger` (drives the real Fabric net via the Node API) |
| `redteam.py` | deterministic hallucination injection (prompt-poison / sycophancy / confident-wrong / collusion) at a chosen dosage |
| `coordinators.py` | baselines: `TrustedOrchestrator` (operator can rewrite votes) and `LedgerlessVoting` (Ed25519 signed votes, no ledger) |
| `run.py` | CLI: load corpus → run under a policy → report metrics |
| `tests/` | coordinator semantics and forgery resistance (`../.venv/bin/python -m pytest -q tests`) |

`LocalLedger.evaluate()` is a byte-for-byte port of the Go `evaluate()`, so offline
results match an on-chain run — the same policy code decides both.

## Quick start (offline — no cluster, no API key)

```bash
pip install -r requirements.txt          # langgraph (anthropic/requests only for live)
python run.py --corpus ../harvester/corpus_balanced.jsonl \
              --policy MAJORITY --reviewer mock --ledger local --accuracy 0.75
```

## Live run (Fabric network + Node API + Claude)

```bash
python run.py --corpus ../harvester/corpus_balanced.jsonl \
              --policy UNANIMOUS --reviewer claude --model claude-sonnet-5 \
              --ledger api --api-url http://localhost:4000 \
              --org-users Org1MSP=appUser,Org2MSP=appUser,Org3MSP=appUser
```

Needs the `agentAction` chaincode deployed and each org's identity registered in
the API wallet (`POST /register`).

## Red-team + re-propose

```bash
# 2-of-3 breaks under collusion; watch blast radius on poisoned cases climb:
python run.py ... --policy MAJORITY   --injection-mode collusion --injection-rate 0.5
# UNANIMOUS contains it — at a conflict-round cost:
python run.py ... --policy UNANIMOUS  --injection-mode collusion --injection-rate 0.5 --max-rounds 3
```

## What the metrics showed (offline, mock reviewer, 48-case corpus)

Reproduced the paper's predicted trade-off end-to-end:

| policy | commit rate | blast radius (clean) | blast on poisoned (collusion@0.5) | accuracy |
|---|---|---|---|---|
| `ANY` | 48/48 | 12.5% | — | 87.5% |
| `MAJORITY` | 44/48 | 4.2% | **66.7%** | 87.5% |
| `ROLE_WEIGHTED` | 37/48 | 2.1% | — | 75.0% |
| `UNANIMOUS` | 23/48 | **0%** | **0%** (1.88 mean rounds) | 47.9%* |

\* single-round UNANIMOUS over-rejects correct-but-non-unanimous cases; the
re-propose loop (`--max-rounds`) recovers some at a conflict-cost. The
**MAJORITY-fails-but-UNANIMOUS-holds crossover under collusion** is the headline
safety result.

> `MockReviewer` uses the ground-truth label only to *simulate* an agent of known
> accuracy — it is a harness self-test, not a classifier. Real runs use
> `ClaudeReviewer`, which reads only `facts` and never sees `held_out`.

## Baselines and the forgery attack (RQ4)

`--ledger` picks where the quorum decision is made: `local` (consensus gate,
offline), `api` (consensus gate on Fabric), `orchestrator` or `ledgerless`.
`--attack-rate r` makes the operator (Org1) try, on a fraction r of cases, to
cast a dissenting organisation's vote as an endorsement before that
organisation votes. The compromised orchestrator condition is this attack run
against `orchestrator`.

```bash
for L in local orchestrator ledgerless; do
  python run.py --corpus ../harvester/corpus_balanced.jsonl --policy MAJORITY --ledger $L --attack-rate 0.5
done
# orchestrator accepts every forged vote; ledgerless and local reject all of them
```

On Fabric, pass one gateway per organisation (`--gateways Org1MSP=http://localhost:4001,...`).
