# AgentAction chaincode

Consensus-gated action approval for cross-organisation multi-agent workflows.
Derived from an `assetTransfer` chaincode.

## Model

Each agent is the client of one Fabric org. A workflow **step** for a **case** is
proposed by one org and voted on by the others; `FinalizeStep` applies the
channel's configured endorsement policy to decide whether the action commits. The
gate lives in chaincode + Fabric consensus — not in a trusted orchestrator — and
every proposal/vote/outcome is a ledger write, so the decision trail is auditable.

Vote *reasons* (LLM rationales) are stored as short strings; the full model output
lives off-chain and is referenced by `PayloadHash`, keeping endorsing peers
deterministic.

## Transactions

| fn | who | effect |
|---|---|---|
| `SetPolicy(policy, totalOrgs, mandatoryMSP, requiredWeight, weightsJSON)` | admin | store the gate config (one Helm value per run) |
| `GetPolicy()` | any | read config (defaults to `MAJORITY`/3) |
| `ProposeStep(caseID, stepID, actionType, payloadHash)` | proposer org | open a step (no auto-endorse) |
| `VoteStep(caseID, stepID, endorse, reason)` | each org | one endorse/reject vote per round |
| `FinalizeStep(caseID, stepID)` | any | apply policy → `COMMITTED` / `REJECTED` / `OPEN` |
| `ReproposeStep(caseID, stepID, actionType, payloadHash)` | any | re-open a rejected step (bumps `Round`, clears votes) — conflict-resolution cost |
| `GetProposal(caseID, stepID)` | any | current step state |
| `GetCaseTrail(caseID)` | any | all steps for a case, sorted |
| `GetStepHistory(caseID, stepID)` | any | full mutation history of a step |

## Policies

| key | commits when |
|---|---|
| `ANY` | ≥1 endorsement |
| `MAJORITY` | > half the orgs endorse (2-of-3) — Fabric default |
| `UNANIMOUS` | all orgs endorse; any dissent → `REJECTED` |
| `ROLE_WEIGHTED` | `mandatoryMSP` endorses **and** endorsing weight ≥ `requiredWeight`; mandatory dissent → veto |

`FinalizeStep` returns `OPEN` when the vote is not yet decisive, `REJECTED` once
quorum is provably unreachable (or all orgs have voted without it).

## Build & test

```bash
go test ./...     # 16 unit tests, no external services (hand-rolled stubs)
go build ./...    # or: docker build -t agent-action-cc .  (chaincode-as-a-service)
```

Deploys the same way as the asset-transfer chaincode (external builder /
chaincode-as-a-service); swap the image in `deploy/chart/cgc` chaincode values.

## Test coverage

Config defaults/validation · propose & vote guards (duplicate, double-vote,
missing step) · all four policy gates · the **blast-radius scenario**
(`TestPoisonedProposerOutvotedUnderMajority` — a hallucinating proposer is
out-voted and the wrong action never commits, with dissent on the trail) ·
case trail / step history / re-propose.
