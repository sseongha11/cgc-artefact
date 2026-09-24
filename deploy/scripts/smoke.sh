#!/usr/bin/env bash
# Exit check on the live testbed:
#   a) register one agent identity per org, each at its own org's gateway
#   b) run N cases under each of the 4 policies on Fabric (mock reviewer: no LLM key
#      needed; the ledger path is what is being tested)
#   c) parity: the offline gate must match Fabric decision for decision
#   d) poisoned agent under MAJORITY, with the case trail read back from the ledger
#   e) forgery: the Org1 gateway must refuse to act for Org2 or Org3
# Logs go to results/raw/smoke-<timestamp>/.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/env.sh"

N="${N:-3}"
TS="$(date +%Y%m%d-%H%M%S)"
OUT="${REPO_ROOT}/results/raw/smoke-${TS}"
PY="${REPO_ROOT}/.venv/bin/python"
CORPUS="${REPO_ROOT}/harvester/corpus_balanced.jsonl"
USERS="Org1MSP=agent-org1,Org2MSP=agent-org2,Org3MSP=agent-org3"
gw() { echo "http://localhost:$((GATEWAY_PORT_BASE + $1))"; }
GATEWAYS="Org1MSP=$(gw 1),Org2MSP=$(gw 2),Org3MSP=$(gw 3)"
LIVE=(--reviewer mock --ledger api --api-url "$(gw 1)" --gateways "${GATEWAYS}" --org-users "${USERS}")
mkdir -p "${OUT}"

PIDS=()
trap 'kill "${PIDS[@]}" 2>/dev/null || true' EXIT
for ORG in 1 2 3; do
  kubectl -n "${NS}" port-forward "svc/gateway-org${ORG}" "$((GATEWAY_PORT_BASE + ORG)):4000" >/dev/null 2>&1 &
  PIDS+=($!)
done
for ORG in 1 2 3; do
  for _ in $(seq 30); do curl -s -o /dev/null "$(gw ${ORG})" && break; sleep 1; done
done

echo "── a) register agent identities at their own gateways"
for ORG in 1 2 3; do
  curl -sf -X POST "$(gw ${ORG})/register" -H 'Content-Type: application/json' \
    -d "{\"org\":\"Org${ORG}MSP\",\"userId\":\"agent-org${ORG}\"}" >/dev/null
  echo "  • agent-org${ORG} @ Org${ORG}MSP via gateway-org${ORG}"
done

cd "${REPO_ROOT}/agents"
echo "── b) ${N} cases x 4 policies on Fabric"
for POLICY in ANY MAJORITY UNANIMOUS ROLE_WEIGHTED; do
  "${PY}" run.py --corpus "${CORPUS}" --limit "${N}" --policy "${POLICY}" "${LIVE[@]}" \
    --run-id "smoke-${TS}-${POLICY}" --out "${OUT}/${POLICY}.jsonl" > "${OUT}/${POLICY}.txt"
  grep -E "committed /|ACCURACY|writes" "${OUT}/${POLICY}.txt" | sed "s/^/  ${POLICY}: /"
done

echo "── c) parity: the offline gate must match Fabric decision for decision"
for POLICY in ANY MAJORITY UNANIMOUS ROLE_WEIGHTED; do
  "${PY}" run.py --corpus "${CORPUS}" --limit "${N}" --policy "${POLICY}" \
    --reviewer mock --ledger local --out "${OUT}/local-${POLICY}.jsonl" >/dev/null
done
"${PY}" - "${OUT}" <<'PYEOF'
import json, sys
out, bad = sys.argv[1], 0
for pol in ["ANY", "MAJORITY", "UNANIMOUS", "ROLE_WEIGHTED"]:
    load = lambda f: [json.loads(l) for l in open(f"{out}/{f}")]
    fab, loc = load(f"{pol}.jsonl"), load(f"local-{pol}.jsonl")
    for a, b in zip(fab, loc):
        ka = (a["committed"], a["rounds"], a["tx_writes"])
        kb = (b["committed"], b["rounds"], b["tx_writes"])
        if ka != kb:
            bad += 1
            print(f"  MISMATCH {pol} {b['case_id']}: fabric={ka} local={kb}")
    print(f"  {pol:13s} {len(fab)} cases  {'match' if not bad else 'see mismatches'}")
sys.exit(1 if bad else 0)
PYEOF

echo "── d) poisoned agent under MAJORITY (overconfident on every case)"
"${PY}" run.py --corpus "${CORPUS}" --limit "${N}" --policy MAJORITY "${LIVE[@]}" \
  --injection-mode confident-wrong --injection-rate 1.0 \
  --run-id "smoke-${TS}-poison" --out "${OUT}/poison.jsonl" > "${OUT}/poison.txt"
grep -E "ACCURACY|poisoned cases" "${OUT}/poison.txt" | sed 's/^/ /'
CASE=$(head -1 "${OUT}/poison.jsonl" | "${PY}" -c 'import json,sys; print(json.load(sys.stdin)["case_id"])')
curl -sfG "$(gw 1)/caseTrail" --data-urlencode "org=Org1MSP" --data-urlencode "userId=agent-org1" \
  --data-urlencode "data[caseID]=${CASE}" > "${OUT}/poison-trail.json"
"${PY}" - "${OUT}/poison-trail.json" <<'PYEOF'
import json, sys
for p in json.load(open(sys.argv[1])):
    print(f"  trail {p['stepID']}: {p['status']} {p['outcome']} under {p['policy']}")
    for msp, v in sorted(p["votes"].items()):
        print(f"    {msp}: {'endorse' if v['endorse'] else 'reject '}  {v['reason']}")
PYEOF

echo "── e) forgery: the Org1 gateway acting for another organisation"
for VICTIM in 2 3; do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$(gw 1)/voteStep" -H 'Content-Type: application/json' \
    -d "{\"org\":\"Org${VICTIM}MSP\",\"userId\":\"agent-org${VICTIM}\",\"data\":{\"caseID\":\"x\",\"stepID\":\"decide\",\"endorse\":true}}")
  echo "  vote as Org${VICTIM}MSP via gateway-org1: HTTP ${CODE}"
  [[ "${CODE}" == 403 ]] || { echo "  FAIL: expected 403"; exit 1; }
done
"${PY}" run.py --corpus "${CORPUS}" --limit "${N}" --policy MAJORITY "${LIVE[@]}" --accuracy 0.5 \
  --attack-rate 1.0 --run-id "smoke-${TS}-forge" --out "${OUT}/forge.jsonl" > "${OUT}/forge.txt"
grep -E "FORGERY" "${OUT}/forge.txt" | sed 's/^/ /' || echo "  (no case had a dissenting victim; raise N)"
if grep -qE "FORGERY  succeeded / attempted   : [1-9]" "${OUT}/forge.txt"; then
  echo "  FAIL: a forged vote was accepted on Fabric"; exit 1
fi

echo "✓ smoke logs in ${OUT}"
