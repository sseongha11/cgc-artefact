# Testbed deployment

A 3-org Hyperledger Fabric 2.5 network on a local kind cluster, running the
`agentaction` chaincode and the Node API that the agents call.

| Layer | Source |
|---|---|
| CAs, certs, Raft orderer, 3 peers (CouchDB), channel `mychannel` | `deploy/fabric/` (phases 0 to 6) |
| `agentaction` chaincode (CCaaS, one server per org) | `deploy/agent-action/` + `chaincode/agent-action/` |
| One API gateway per organisation | `api/` (image built here) + `deploy/agent-action/gateway.yaml.tpl` |

The CGC demo pieces (`basic` chaincode, UI, Explorer, monitoring, ingress) are
not deployed. They are not needed for the experiments.

## Run

```sh
./deploy/scripts/up.sh      # ~5–10 min first time (image pulls); idempotent
./deploy/scripts/smoke.sh   # identities, 4 policies on chain, parity, poison demo, forgery check
./deploy/scripts/down.sh    # delete the cluster
```

Needs Docker running, plus `kind`, `kubectl` and the repo `.venv`.

## Notes

- **Chaincode code change:** just rerun `up.sh`. With chaincode-as-a-service the package holds only `connection.json`, so new code means a new image and a pod restart. Bump `CC_SEQUENCE`/`CC_VERSION` only when the *definition* changes (e.g. endorsement policy).
- **Gateways:** each organisation has its own gateway (`gateway-org{1,2,3}`, local ports 4001 to 4003 in `smoke.sh`). A gateway mounts only the public connection profiles and its own wallet, and `GATEWAY_MSP` makes it refuse requests for other organisations. A single shared API would hold every wallet and so be a trusted party, which the paper's claim rules out.
- **Agent identities:** each is registered with its own org's CA through its own gateway. Registration is idempotent: if the CA already knows the identity, the org admin resets its secret and enrols it again.
- **Package ids:** the install job reuses the package id from the approved definition. Rebuilding a package yields a new id (timestamps in the tarball), so it must never be picked from `queryinstalled`.
- **Repeat runs on one ledger:** pass `--run-id` to `agents/run.py`. Otherwise decided steps collide.
- **Endorsement:** transactions use the channel default endorsement policy (majority of orgs' peers). The research variable, the agent-vote policy, is set on-chain with `SetPolicy`.
