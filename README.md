# Consensus Gated Coordination of LLM Agents Across Organisations

Artefact for an anonymous AAMAS 2027 submission. LLM agents acting for different
organisations coordinate without a trusted orchestrator: every proposed action is
a Hyperledger Fabric transaction, each organisation votes under its own MSP
identity, and the action commits only when the votes satisfy an endorsement
policy (ANY, MAJORITY, UNANIMOUS or ROLE WEIGHTED) enforced in chaincode.

## Layout

```
agents/                 LangGraph runtime: reviewers, fault injection, coordinators, ledger clients, CLI
chaincode/agent-action/ Go chaincode: ProposeStep / VoteStep / FinalizeStep and the four policies, with unit tests
api/                    Node gateway, one per organisation, holding only that organisation's wallet
deploy/                 kind cluster: Fabric core in deploy/fabric/, chaincode and gateways in deploy/agent-action/
experiments/            Every experiment, figure, table and number in the paper
datasets/contractnli/   Contract review corpus (ContractNLI, CC BY 4.0) and the script that builds it
harvester/              Planning appeal corpus (Planning Inspectorate, OGL v3.0) and the harvester
results/                Summary results: runs.csv, live network, sensitivity, agreement, scaling, significance
```

## Setup

```sh
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r agents/requirements.txt matplotlib numpy
```

## Data archive

Per case outputs of the full factorial (`results/full/<domain>/<panel>/*.jsonl`) and
the stored LLM responses (`results/cache/`) are too large for the repository and are
provided as `cgc-artefact-data.tar.gz`. Unpack it at the repository root:

```sh
tar xzf cgc-artefact-data.tar.gz
```

With the cache in place every rerun reuses the identical stored answers, so no API key
is needed and results are reproduced exactly.

## Reproducing the paper

```sh
cd experiments
../.venv/bin/python figures.py        # figures and tables (written to paper/)
../.venv/bin/python text_numbers.py   # every number quoted in the text
../.venv/bin/python significance.py   # bootstrap tests over cases
../.venv/bin/python agreement.py      # equivocation under signed broadcast (RQ4)
../.venv/bin/python attacks.py        # forgery and omission (RQ4)
../.venv/bin/python scaling.py        # consortia of 3 to 9 organisations
../.venv/bin/python full.py           # the full factorial (uses the cache)
```

`sensitivity.py` reruns the unfiltered contract corpus; it calls OpenRouter unless its
responses are cached, and reads the key from `OPENROUTER_API_KEY` in a `.env` file.

## Offline smoke test (no cluster, no API key)

```sh
cd agents
../.venv/bin/python run.py --corpus ../harvester/corpus_balanced.jsonl \
    --policy MAJORITY --reviewer mock --ledger local --accuracy 0.75
```

## Chaincode tests

```sh
cd chaincode/agent-action && go test ./...
```

## Live Fabric network

Needs Docker, `kind`, `kubectl` and the `.venv` above; see `deploy/README.md`.

```sh
./deploy/scripts/up.sh      # three organisations, Raft orderer, chaincode as a service, one gateway per organisation
./deploy/scripts/smoke.sh   # identities, the four policies on chain, parity with the local gate, forgery check
cd experiments && ../.venv/bin/python live.py && ../.venv/bin/python throughput.py
./deploy/scripts/down.sh
```

The testbed uses default administrator secrets for the certificate authorities and places
every organisation on one cluster; it is for measurement, not production.

## Licences

ContractNLI: Creative Commons Attribution 4.0. Planning appeal decisions: Crown copyright,
Open Government Licence v3.0.
