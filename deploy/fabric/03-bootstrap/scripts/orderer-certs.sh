#!/bin/bash
set -euo pipefail

# Idempotency: bail out if the orderer Admin MSP already exists from a previous run.
DONE_MARKER=/organizations/ordererOrganizations/example.com/users/Admin@example.com/msp/keystore
if [ -d "${DONE_MARKER}" ] && [ "$(ls -A "${DONE_MARKER}" 2>/dev/null)" ]; then
  echo "orderer certs already generated, skipping"
  exit 0
fi

mkdir -p /organizations/ordererOrganizations/example.com
export FABRIC_CA_CLIENT_HOME=/organizations/ordererOrganizations/example.com

# Wait for orderer CA's self-signed TLS cert to appear on the PVC.
until [ -f /organizations/fabric-ca/ordererOrg/tls-cert.pem ]; do
  echo "waiting for ca-orderer tls-cert.pem..."; sleep 2
done

fabric-ca-client enroll \
  -u https://admin:adminpw@ca-orderer:10054 \
  --caname ca-orderer \
  --tls.certfiles /organizations/fabric-ca/ordererOrg/tls-cert.pem

cat > /organizations/ordererOrganizations/example.com/msp/config.yaml <<EOF
NodeOUs:
  Enable: true
  ClientOUIdentifier:
    Certificate: cacerts/ca-orderer-10054-ca-orderer.pem
    OrganizationalUnitIdentifier: client
  PeerOUIdentifier:
    Certificate: cacerts/ca-orderer-10054-ca-orderer.pem
    OrganizationalUnitIdentifier: peer
  AdminOUIdentifier:
    Certificate: cacerts/ca-orderer-10054-ca-orderer.pem
    OrganizationalUnitIdentifier: admin
  OrdererOUIdentifier:
    Certificate: cacerts/ca-orderer-10054-ca-orderer.pem
    OrganizationalUnitIdentifier: orderer
EOF

fabric-ca-client register --caname ca-orderer --id.name orderer --id.secret ordererpw --id.type orderer \
  --tls.certfiles /organizations/fabric-ca/ordererOrg/tls-cert.pem
fabric-ca-client register --caname ca-orderer --id.name ordererAdmin --id.secret ordererAdminpw --id.type admin \
  --tls.certfiles /organizations/fabric-ca/ordererOrg/tls-cert.pem

mkdir -p /organizations/ordererOrganizations/example.com/orderers/orderer.example.com

fabric-ca-client enroll -u https://orderer:ordererpw@ca-orderer:10054 --caname ca-orderer \
  -M /organizations/ordererOrganizations/example.com/orderers/orderer.example.com/msp \
  --csr.hosts orderer.example.com --csr.hosts localhost --csr.hosts ca-orderer --csr.hosts orderer \
  --tls.certfiles /organizations/fabric-ca/ordererOrg/tls-cert.pem

cp /organizations/ordererOrganizations/example.com/msp/config.yaml \
   /organizations/ordererOrganizations/example.com/orderers/orderer.example.com/msp/config.yaml

fabric-ca-client enroll -u https://orderer:ordererpw@ca-orderer:10054 --caname ca-orderer \
  -M /organizations/ordererOrganizations/example.com/orderers/orderer.example.com/tls \
  --enrollment.profile tls \
  --csr.hosts orderer.example.com --csr.hosts localhost --csr.hosts ca-orderer --csr.hosts orderer \
  --tls.certfiles /organizations/fabric-ca/ordererOrg/tls-cert.pem

cp /organizations/ordererOrganizations/example.com/orderers/orderer.example.com/tls/tlscacerts/* \
   /organizations/ordererOrganizations/example.com/orderers/orderer.example.com/tls/ca.crt
cp /organizations/ordererOrganizations/example.com/orderers/orderer.example.com/tls/signcerts/* \
   /organizations/ordererOrganizations/example.com/orderers/orderer.example.com/tls/server.crt
cp /organizations/ordererOrganizations/example.com/orderers/orderer.example.com/tls/keystore/* \
   /organizations/ordererOrganizations/example.com/orderers/orderer.example.com/tls/server.key

mkdir -p /organizations/ordererOrganizations/example.com/orderers/orderer.example.com/msp/tlscacerts
cp /organizations/ordererOrganizations/example.com/orderers/orderer.example.com/tls/tlscacerts/* \
   /organizations/ordererOrganizations/example.com/orderers/orderer.example.com/msp/tlscacerts/tlsca.example.com-cert.pem

mkdir -p /organizations/ordererOrganizations/example.com/msp/tlscacerts
cp /organizations/ordererOrganizations/example.com/orderers/orderer.example.com/tls/tlscacerts/* \
   /organizations/ordererOrganizations/example.com/msp/tlscacerts/tlsca.example.com-cert.pem

mkdir -p /organizations/ordererOrganizations/example.com/users/Admin@example.com
fabric-ca-client enroll -u https://ordererAdmin:ordererAdminpw@ca-orderer:10054 --caname ca-orderer \
  -M /organizations/ordererOrganizations/example.com/users/Admin@example.com/msp \
  --tls.certfiles /organizations/fabric-ca/ordererOrg/tls-cert.pem

cp /organizations/ordererOrganizations/example.com/msp/config.yaml \
   /organizations/ordererOrganizations/example.com/users/Admin@example.com/msp/config.yaml

echo "orderer certs done"
