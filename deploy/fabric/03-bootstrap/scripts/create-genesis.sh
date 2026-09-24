#!/bin/bash
set -euo pipefail
export FABRIC_CFG_PATH=/configtx
mkdir -p /system-genesis-block /channel-artifacts

# Fabric 2.5 dropped system-channel; we use a channel-application genesis block.
configtxgen -profile RcpgChannelGenesis -channelID mychannel \
  -outputBlock /channel-artifacts/mychannel.block

# Anchor peer update transactions for each org, applied later from inside each peer.
for orgmsp in Org1MSP Org2MSP Org3MSP; do
  configtxgen -profile RcpgChannel \
    -outputAnchorPeersUpdate "/channel-artifacts/${orgmsp}anchors.tx" \
    -channelID mychannel -asOrg "${orgmsp}"
done

echo "channel artifacts done"
ls -la /channel-artifacts
