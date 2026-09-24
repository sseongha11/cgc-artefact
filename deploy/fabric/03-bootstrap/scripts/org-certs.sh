#!/bin/bash
# Generic org-cert generator. Pass org index (1, 2, or 3) and CA port as arguments.
# Replaces the per-org duplicate scripts in the original repo.
set -euo pipefail

ORG_INDEX="${1:?usage: org-certs.sh <ORG_INDEX> <CA_PORT>}"
CA_PORT="${2:?usage: org-certs.sh <ORG_INDEX> <CA_PORT>}"

ORG_DOMAIN="org${ORG_INDEX}.example.com"
CA_NAME="ca-org${ORG_INDEX}"
ORG_HOME="/organizations/peerOrganizations/${ORG_DOMAIN}"
CA_TLS_CERTS="/organizations/fabric-ca/org${ORG_INDEX}/tls-cert.pem"

# Idempotency: skip if the org's Admin MSP keystore already has content.
DONE_MARKER="${ORG_HOME}/users/Admin@${ORG_DOMAIN}/msp/keystore"
if [ -d "${DONE_MARKER}" ] && [ "$(ls -A "${DONE_MARKER}" 2>/dev/null)" ]; then
  echo "org${ORG_INDEX} certs already generated, skipping"
  exit 0
fi

mkdir -p "${ORG_HOME}"
export FABRIC_CA_CLIENT_HOME="${ORG_HOME}"

until [ -f "${CA_TLS_CERTS}" ]; do
  echo "waiting for ${CA_NAME} tls-cert.pem..."; sleep 2
done

fabric-ca-client enroll -u "https://admin:adminpw@${CA_NAME}:${CA_PORT}" \
  --caname "${CA_NAME}" --tls.certfiles "${CA_TLS_CERTS}"

cat > "${ORG_HOME}/msp/config.yaml" <<EOF
NodeOUs:
  Enable: true
  ClientOUIdentifier:
    Certificate: cacerts/${CA_NAME}-${CA_PORT}-${CA_NAME}.pem
    OrganizationalUnitIdentifier: client
  PeerOUIdentifier:
    Certificate: cacerts/${CA_NAME}-${CA_PORT}-${CA_NAME}.pem
    OrganizationalUnitIdentifier: peer
  AdminOUIdentifier:
    Certificate: cacerts/${CA_NAME}-${CA_PORT}-${CA_NAME}.pem
    OrganizationalUnitIdentifier: admin
  OrdererOUIdentifier:
    Certificate: cacerts/${CA_NAME}-${CA_PORT}-${CA_NAME}.pem
    OrganizationalUnitIdentifier: orderer
EOF

fabric-ca-client register --caname "${CA_NAME}" --id.name peer0 --id.secret peer0pw --id.type peer \
  --tls.certfiles "${CA_TLS_CERTS}"
fabric-ca-client register --caname "${CA_NAME}" --id.name user1 --id.secret user1pw --id.type client \
  --tls.certfiles "${CA_TLS_CERTS}"
fabric-ca-client register --caname "${CA_NAME}" --id.name "org${ORG_INDEX}admin" --id.secret "org${ORG_INDEX}adminpw" --id.type admin \
  --tls.certfiles "${CA_TLS_CERTS}"

PEER_DIR="${ORG_HOME}/peers/peer0.${ORG_DOMAIN}"
fabric-ca-client enroll -u "https://peer0:peer0pw@${CA_NAME}:${CA_PORT}" --caname "${CA_NAME}" \
  -M "${PEER_DIR}/msp" \
  --csr.hosts "peer0.${ORG_DOMAIN}" --csr.hosts "peer0-org${ORG_INDEX}" \
  --tls.certfiles "${CA_TLS_CERTS}"

cp "${ORG_HOME}/msp/config.yaml" "${PEER_DIR}/msp/config.yaml"

fabric-ca-client enroll -u "https://peer0:peer0pw@${CA_NAME}:${CA_PORT}" --caname "${CA_NAME}" \
  -M "${PEER_DIR}/tls" --enrollment.profile tls \
  --csr.hosts "peer0.${ORG_DOMAIN}" --csr.hosts "peer0-org${ORG_INDEX}" \
  --csr.hosts "${CA_NAME}" --csr.hosts localhost \
  --tls.certfiles "${CA_TLS_CERTS}"

cp "${PEER_DIR}/tls/tlscacerts/"* "${PEER_DIR}/tls/ca.crt"
cp "${PEER_DIR}/tls/signcerts/"*  "${PEER_DIR}/tls/server.crt"
cp "${PEER_DIR}/tls/keystore/"*   "${PEER_DIR}/tls/server.key"

mkdir -p "${ORG_HOME}/msp/tlscacerts" "${ORG_HOME}/tlsca" "${ORG_HOME}/ca"
cp "${PEER_DIR}/tls/tlscacerts/"* "${ORG_HOME}/msp/tlscacerts/ca.crt"
cp "${PEER_DIR}/tls/tlscacerts/"* "${ORG_HOME}/tlsca/tlsca.${ORG_DOMAIN}-cert.pem"
cp "${PEER_DIR}/msp/cacerts/"*    "${ORG_HOME}/ca/ca.${ORG_DOMAIN}-cert.pem"

USER_DIR="${ORG_HOME}/users/User1@${ORG_DOMAIN}/msp"
fabric-ca-client enroll -u "https://user1:user1pw@${CA_NAME}:${CA_PORT}" --caname "${CA_NAME}" \
  -M "${USER_DIR}" --tls.certfiles "${CA_TLS_CERTS}"
cp "${ORG_HOME}/msp/config.yaml" "${USER_DIR}/config.yaml"

ADMIN_DIR="${ORG_HOME}/users/Admin@${ORG_DOMAIN}/msp"
fabric-ca-client enroll -u "https://org${ORG_INDEX}admin:org${ORG_INDEX}adminpw@${CA_NAME}:${CA_PORT}" --caname "${CA_NAME}" \
  -M "${ADMIN_DIR}" --tls.certfiles "${CA_TLS_CERTS}"
cp "${ORG_HOME}/msp/config.yaml" "${ADMIN_DIR}/config.yaml"

echo "org${ORG_INDEX} certs done"
