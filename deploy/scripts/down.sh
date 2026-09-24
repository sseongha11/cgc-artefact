#!/usr/bin/env bash
# Deletes the kind cluster (all ledger state is lost). Images stay on the Docker host.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/env.sh"
kind delete cluster --name "${CLUSTER}"
