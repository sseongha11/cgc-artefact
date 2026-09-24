#!/usr/bin/env bash
# Checks the aligned endorsement policy on the live network (honest chaincode).
# A case where Org3 dissents is finalised twice: once with endorsements from only
# two organisations' peers, which the aligned policy must invalidate, and once
# with all three, which must be valid and record the honest outcome (REJECTED
# under UNANIMOUS). Output: results/live/endorsement_check.txt
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/deploy/env.sh"
OUT="${REPO_ROOT}/results/live/endorsement_check.txt"
CASE="endorse-check-$(date +%s)"
PY="${REPO_ROOT}/.venv/bin/python"

PIDS=(); trap 'kill "${PIDS[@]}" 2>/dev/null || true' EXIT
for O in 1 2 3; do
  kubectl -n "${NS}" port-forward "svc/gateway-org${O}" "$((4000 + O)):4000" >/dev/null 2>&1 & PIDS+=($!)
done
sleep 4
"${PY}" - "${CASE}" <<'PYEOF'
import sys, requests
case = sys.argv[1]
gw = {f"Org{i}MSP": f"http://localhost:{4000+i}" for i in (1, 2, 3)}
user = {f"Org{i}MSP": f"agent-org{i}" for i in (1, 2, 3)}
def post(org, path, data):
    r = requests.post(gw[org] + path, json={"org": org, "userId": user[org], "data": data}, timeout=120)
    r.raise_for_status()
for o in gw:
    requests.post(gw[o] + "/register", json={"org": o, "userId": user[o]}, timeout=120).raise_for_status()
post("Org1MSP", "/setPolicy", {"policy": "UNANIMOUS", "totalOrgs": 3})
post("Org1MSP", "/proposeStep", {"caseID": case, "stepID": "decide", "actionType": "APPROVE"})
for o, e in (("Org1MSP", True), ("Org2MSP", True), ("Org3MSP", False)):
    post(o, "/voteStep", {"caseID": case, "stepID": "decide", "endorse": e, "reason": "check"})
print("case ready:", case)
PYEOF

run_invoke() {  # run_invoke <job-name> <peer orgs...>
  local name="$1"; shift
  local peers=""
  for O in "$@"; do
    peers="${peers} --peerAddresses peer0-org${O}:7051 --tlsRootCertFiles /organizations/peerOrganizations/org${O}.example.com/peers/peer0.org${O}.example.com/tls/ca.crt"
  done
  kubectl -n "${NS}" delete job "${name}" --ignore-not-found >/dev/null
  cat <<YAML | kubectl apply -f - >/dev/null
apiVersion: batch/v1
kind: Job
metadata: { name: ${name}, namespace: ${NS} }
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      volumes: [{ name: data, persistentVolumeClaim: { claimName: cgc-data } }]
      containers:
        - name: tools
          image: hyperledger/fabric-tools:2.5
          command: ["/bin/bash", "-c"]
          env:
            - { name: FABRIC_CFG_PATH, value: /etc/hyperledger/fabric }
            - { name: CORE_PEER_LOCALMSPID, value: Org1MSP }
            - { name: CORE_PEER_TLS_ENABLED, value: "true" }
            - { name: CORE_PEER_TLS_ROOTCERT_FILE, value: /organizations/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/ca.crt }
            - { name: CORE_PEER_MSPCONFIGPATH, value: /organizations/peerOrganizations/org1.example.com/users/Admin@org1.example.com/msp }
            - { name: CORE_PEER_ADDRESS, value: "peer0-org1:7051" }
          args:
            - |
              peer chaincode invoke -o orderer:7050 --tls \
                --cafile /organizations/ordererOrganizations/example.com/orderers/orderer.example.com/msp/tlscacerts/tlsca.example.com-cert.pem \
                -C ${CHANNEL_NAME} -n ${CC_NAME} ${peers} --waitForEvent \
                -c '{"function":"FinalizeStep","Args":["${CASE}","decide"]}' 2>&1 | tail -3
              echo "state:"; peer chaincode query -C ${CHANNEL_NAME} -n ${CC_NAME} \
                -c '{"function":"GetProposal","Args":["${CASE}","decide"]}' 2>&1 | grep -o '"status":"[A-Z]*"'
          volumeMounts: [{ name: data, mountPath: /organizations, subPath: organizations }]
YAML
  kubectl -n "${NS}" wait --for=condition=complete --timeout=180s "job/${name}" >/dev/null 2>&1 \
    || kubectl -n "${NS}" wait --for=condition=failed --timeout=10s "job/${name}" >/dev/null 2>&1 || true
  kubectl -n "${NS}" logs "job/${name}" 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | tail -5
}

{
  echo "== definition"; kubectl -n "${NS}" exec deploy/peer0-org1 -c peer -- sh -c \
    "CORE_PEER_MSPCONFIGPATH=/organizations/peerOrganizations/org1.example.com/users/Admin@org1.example.com/msp peer lifecycle chaincode querycommitted -C ${CHANNEL_NAME} -n ${CC_NAME}" 2>&1 | tail -1
  echo "== FinalizeStep endorsed by Org1 and Org2 only"; run_invoke endorse-check-two 1 2
  echo "== FinalizeStep endorsed by all three"; run_invoke endorse-check-three 1 2 3
} | tee "${OUT}"
