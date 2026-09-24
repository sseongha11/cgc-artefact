# Deploy environment for the consensus-gated agents testbed.
# `source` from deploy/scripts/*.sh.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export REPO_ROOT

# Fabric core manifests (CAs, orderer, peers, channel) come from the CGC repo.
export FABRIC_DEPLOY="${FABRIC_DEPLOY:-${REPO_ROOT}/deploy/fabric}"

export CLUSTER="${CLUSTER:-cgc}"
export NS="${NS:-cgc}"
export CHANNEL_NAME="${CHANNEL_NAME:-mychannel}"

# AgentAction chaincode definition. Bump CC_SEQUENCE (and CC_VERSION) on every
# chaincode upgrade; the lifecycle rejects a re-approve at the same sequence.
export CC_NAME="${CC_NAME:-agentaction}"
export CC_VERSION="${CC_VERSION:-1.0}"
export CC_SEQUENCE="${CC_SEQUENCE:-1}"
# Native endorsement policy for the chaincode definition; empty = channel default
# (MAJORITY). Align it with the vote gate, e.g. for UNANIMOUS:
#   CC_SIGNATURE_POLICY="AND('Org1MSP.peer','Org2MSP.peer','Org3MSP.peer')"
export CC_SIGNATURE_POLICY="${CC_SIGNATURE_POLICY:-}"

# Locally built images, side-loaded into kind (no registry push).
export IMG_CC="${IMG_CC:-cgc/agentaction-cc:local}"
export IMG_API="${IMG_API:-cgc/api:local}"

# Local ports for the gateway port forwards: Org1 -> 4001, Org2 -> 4002, Org3 -> 4003.
export GATEWAY_PORT_BASE="${GATEWAY_PORT_BASE:-4000}"
