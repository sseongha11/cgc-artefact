#!/bin/bash
# Build connection-profile JSON files for orgs 1/2/3 and drop them on the PVC at
# /connection-profile/. The API pod mounts this directory and reads the JSONs.
# Idempotent: regenerates even if files exist (cheap, lets us tweak the template).
set -euo pipefail

mkdir -p /connection-profile

one_line_pem() {
  # Emit each PEM line followed by literal backslash-n so the resulting JSON
  # string parses back to a real newline. (Single backslash here = JSON's \n.)
  awk 'NF {printf "%s\\n", $0;}' "$1"
}

write_ccp() {
  local ORG="$1"
  local P0PORT="$2"
  local CAPORT="$3"
  local PEERPEM="/organizations/peerOrganizations/org${ORG}.example.com/tlsca/tlsca.org${ORG}.example.com-cert.pem"
  local CAPEM="/organizations/peerOrganizations/org${ORG}.example.com/ca/ca.org${ORG}.example.com-cert.pem"

  local PP CP
  PP=$(one_line_pem "$PEERPEM")
  CP=$(one_line_pem "$CAPEM")

  cat > "/connection-profile/connection-org${ORG}.json" <<EOF
{
  "name": "cgc-network-org${ORG}",
  "version": "1.0.0",
  "client": {
    "organization": "Org${ORG}",
    "connection": { "timeout": { "peer": { "endorser": "300" } } }
  },
  "organizations": {
    "Org${ORG}": {
      "mspid": "Org${ORG}MSP",
      "peers": ["peer0-org${ORG}"],
      "certificateAuthorities": ["ca-org${ORG}"]
    }
  },
  "peers": {
    "peer0-org${ORG}": {
      "url": "grpcs://peer0-org${ORG}:${P0PORT}",
      "tlsCACerts": { "pem": "${PP}" },
      "grpcOptions": {
        "ssl-target-name-override": "peer0-org${ORG}",
        "hostnameOverride": "peer0-org${ORG}"
      }
    }
  },
  "certificateAuthorities": {
    "ca-org${ORG}": {
      "url": "https://ca-org${ORG}:${CAPORT}",
      "caName": "ca-org${ORG}",
      "tlsCACerts": { "pem": ["${CP}"] },
      "httpOptions": { "verify": false }
    }
  }
}
EOF
  echo "wrote /connection-profile/connection-org${ORG}.json"
}

write_ccp 1 7051 7054
write_ccp 2 7051 8054
write_ccp 3 7051 9054

echo "all connection profiles written"
