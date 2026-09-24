#!/usr/bin/env bash
# Brings up the testbed end to end on a local kind cluster. Idempotent.
#
#   1. kind cluster
#   2. build + side-load images (agentaction chaincode, API)
#   3. Fabric core from CGC: CAs, certs, orderer, 3 peers, channel
#   4. agentaction chaincode: install, run CCaaS servers, approve + commit
#   5. one API gateway per organisation (own wallet, refuses other orgs)
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/env.sh"

phase() { echo; echo "════════ $* ════════"; }
wait_deploy() { kubectl -n "${NS}" rollout status "deployment/$1" --timeout="${2:-240s}"; }
run_job() {  # run_job <name> <manifest-on-stdin>
  kubectl -n "${NS}" delete job "$1" --ignore-not-found
  kubectl apply -f -
  kubectl -n "${NS}" wait --for=condition=complete --timeout=600s "job/$1" \
    || { kubectl -n "${NS}" logs "job/$1" --tail=200 || true; exit 1; }
}

[[ -d "${FABRIC_DEPLOY}" ]] || { echo "CGC deploy dir not found: ${FABRIC_DEPLOY}"; exit 1; }

#-----------------------------------------------------------------------------
phase "1. kind cluster '${CLUSTER}'"
if ! kind get clusters | grep -qx "${CLUSTER}"; then
  kind create cluster --name "${CLUSTER}" --config "${FABRIC_DEPLOY}/kind-cluster.yaml"
fi
kubectl config use-context "kind-${CLUSTER}"

#-----------------------------------------------------------------------------
phase "2. build + load images"
docker build -t "${IMG_CC}"  "${REPO_ROOT}/chaincode/agent-action"
docker build -t "${IMG_API}" "${REPO_ROOT}/api"
kind load docker-image "${IMG_CC}" "${IMG_API}" --name "${CLUSTER}"
CC_IMAGE_ID=$(docker image inspect -f '{{.Id}}' "${IMG_CC}" | cut -c8-19)
API_IMAGE_ID=$(docker image inspect -f '{{.Id}}' "${IMG_API}" | cut -c8-19)

#-----------------------------------------------------------------------------
phase "3. Fabric core (phases 0 to 6)"
cd "${FABRIC_DEPLOY}"
kubectl apply -f 00-namespace.yaml -f 01-storage/pvc.yaml
kubectl apply -f 02-ca/
for ca in ca-org1 ca-org2 ca-org3 ca-orderer; do wait_deploy "${ca}" 180s; done

kubectl -n "${NS}" create configmap cgc-bootstrap-scripts --from-file=03-bootstrap/scripts/ \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "${NS}" create configmap cgc-configtx --from-file=03-bootstrap/configtx/configtx.yaml \
  --dry-run=client -o yaml | kubectl apply -f -
run_job cgc-certs     < 03-bootstrap/certs-job.yaml
run_job cgc-artifacts < 03-bootstrap/artifacts-job.yaml

kubectl apply -f 04-orderer/orderer.yaml
wait_deploy orderer 180s
run_job cgc-osnadmin-join < 04-orderer/osnadmin-join-job.yaml

kubectl apply -f 05-peers/builders-config.yaml \
  -f 05-peers/peer-org1.yaml -f 05-peers/peer-org2.yaml -f 05-peers/peer-org3.yaml
# CouchDB (Erlang) sizes its port table from the fd limit, which is unlimited on
# recent kind nodes, and gets OOM-killed at the 512Mi limit. Cap it. No-op if set.
for p in peer0-org1 peer0-org2 peer0-org3; do
  kubectl -n "${NS}" set env "deployment/${p}" -c couchdb ERL_FLAGS='+Q 65536'
done
for p in peer0-org1 peer0-org2 peer0-org3; do wait_deploy "${p}"; done

# Joining is not idempotent; skip if peer0-org1 is already on the channel.
ADMIN1_MSP=/organizations/peerOrganizations/org1.example.com/users/Admin@org1.example.com/msp
if kubectl -n "${NS}" exec deploy/peer0-org1 -c peer -- sh -c \
     "CORE_PEER_MSPCONFIGPATH=${ADMIN1_MSP} peer channel list 2>/dev/null" | grep -qx "${CHANNEL_NAME}"; then
  echo "  • peers already joined ${CHANNEL_NAME}"
else
  run_job cgc-channel < 06-channel/channel-job.yaml
fi

#-----------------------------------------------------------------------------
phase "4. agentaction chaincode (seq ${CC_SEQUENCE})"
cd "${REPO_ROOT}/deploy/agent-action"
sed -e "s|__CC_NAME__|${CC_NAME}|g" -e "s|__CC_SEQUENCE__|${CC_SEQUENCE}|g" -e "s|__CHANNEL__|${CHANNEL_NAME}|g" \
  install-job.yaml | run_job agentaction-install
for ORG in 1 2 3; do
  PKG=$(kubectl -n "${NS}" logs job/agentaction-install | awk -F= "/^org${ORG} package_id=/ {print \$2}")
  echo "  • Org${ORG} package id: ${PKG}"
  # CCaaS: the package holds only connection.json, so new chaincode code is just
  # a new image (tracked by __IMAGE_ID__); no lifecycle upgrade is needed.
  sed -e "s|__ORG__|${ORG}|g" -e "s|__IMAGE__|${IMG_CC}|g" -e "s|__IMAGE_ID__|${CC_IMAGE_ID}|g" \
      -e "s|__PACKAGE_ID__|${PKG}|g" chaincode.yaml.tpl | kubectl apply -f -
done
for ORG in 1 2 3; do wait_deploy "agentaction-org${ORG}" 180s; done

if kubectl -n "${NS}" exec deploy/peer0-org1 -c peer -- sh -c \
     "CORE_PEER_MSPCONFIGPATH=${ADMIN1_MSP} peer lifecycle chaincode querycommitted -C ${CHANNEL_NAME} -n ${CC_NAME} 2>/dev/null" \
   | grep -q "Sequence: ${CC_SEQUENCE},"; then
  echo "  • ${CC_NAME} seq ${CC_SEQUENCE} already committed"
else
  sed -e "s|__CC_NAME__|${CC_NAME}|g" -e "s|__CC_VERSION__|${CC_VERSION}|g" \
      -e "s|__CC_SEQUENCE__|${CC_SEQUENCE}|g" -e "s|__CHANNEL__|${CHANNEL_NAME}|g" \
      -e "s|__SIG_POLICY__|${CC_SIGNATURE_POLICY}|g" \
      approve-commit-job.yaml | run_job agentaction-approve-commit
fi

#-----------------------------------------------------------------------------
phase "5. gateways (one per organisation)"
# The single shared API held every org's wallet, which made it a trusted party.
kubectl -n "${NS}" delete deployment/api service/api --ignore-not-found
for ORG in 1 2 3; do
  sed -e "s|__ORG__|${ORG}|g" -e "s|__IMAGE__|${IMG_API}|g" -e "s|__IMAGE_ID__|${API_IMAGE_ID}|g" \
    "${REPO_ROOT}/deploy/agent-action/gateway.yaml.tpl" | kubectl apply -f -
done
for ORG in 1 2 3; do wait_deploy "gateway-org${ORG}" 180s; done
# A Service can still route to a terminating pod; wait until only new pods remain.
for _ in $(seq 90); do
  kubectl -n "${NS}" get pods --no-headers | grep -q Terminating || break
  sleep 2
done

phase "✓ testbed is up"
kubectl -n "${NS}" get pods
echo
echo "Next: deploy/scripts/smoke.sh"
