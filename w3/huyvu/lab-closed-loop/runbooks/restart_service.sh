#!/usr/bin/env bash
# restart_service.sh — restart a Docker Compose service container
#
# Usage:
#   bash restart_service.sh --service <name> [--dry-run]
#
# The container name convention used by the Ronki lab stack is:
#   ronki-<service>   (e.g., ronki-payment-svc, ronki-checkout-svc)
#
# Exit codes:
#   0 = success (or dry-run)
#   1 = failure

set -euo pipefail

SERVICE=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --service)  SERVICE="$2"; shift 2 ;;
    --dry-run)  DRY_RUN=true; shift ;;
    *) echo "[restart_service] Unknown argument: $1"; exit 1 ;;
  esac
done

if [[ -z "$SERVICE" ]]; then
  echo "[restart_service] ERROR: --service <name> is required"
  exit 1
fi

CONTAINER="ronki-${SERVICE}"

# ── Dry-run mode ──────────────────────────────────────────────────────────────
if $DRY_RUN; then
  echo "[DRY-RUN] would execute: docker restart $CONTAINER"
  exit 0
fi

# ── Real execution ────────────────────────────────────────────────────────────
echo "[restart_service] Restarting container: $CONTAINER ..."

if ! docker inspect "$CONTAINER" > /dev/null 2>&1; then
  echo "[restart_service] Container $CONTAINER not found — attempting docker start ..."
  docker start "$CONTAINER"
else
  docker restart "$CONTAINER"
fi

echo "[restart_service] Waiting 5 s for $CONTAINER to stabilise ..."
sleep 5

STATUS=$(docker inspect --format '{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo "missing")
if [[ "$STATUS" == "running" ]]; then
  echo "[restart_service] $CONTAINER is running. SUCCESS."
  exit 0
else
  echo "[restart_service] ERROR: $CONTAINER status=$STATUS after restart attempt."
  exit 1
fi
