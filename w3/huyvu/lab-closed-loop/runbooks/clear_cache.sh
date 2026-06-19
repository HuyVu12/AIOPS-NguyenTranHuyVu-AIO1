#!/usr/bin/env bash
# clear_cache.sh — flush in-memory cache of a service via SIGHUP
#
# Usage:
#   bash clear_cache.sh --service <name> [--dry-run]
#
# Convention: many servers reload config / flush caches on SIGHUP.
# In the Ronki lab the mock services handle SIGHUP as a cache-clear signal.
# An alternative would be calling a dedicated /admin/cache/clear HTTP endpoint.
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
    *) echo "[clear_cache] Unknown argument: $1"; exit 1 ;;
  esac
done

if [[ -z "$SERVICE" ]]; then
  echo "[clear_cache] ERROR: --service <name> is required"
  exit 1
fi

CONTAINER="ronki-${SERVICE}"

# ── Dry-run mode ──────────────────────────────────────────────────────────────
if $DRY_RUN; then
  echo "[DRY-RUN] would execute: docker kill --signal=SIGHUP $CONTAINER"
  exit 0
fi

# ── Real execution ────────────────────────────────────────────────────────────
echo "[clear_cache] Sending SIGHUP to $CONTAINER to flush cache ..."

if ! docker inspect "$CONTAINER" > /dev/null 2>&1; then
  echo "[clear_cache] ERROR: Container $CONTAINER not found."
  exit 1
fi

docker kill --signal=SIGHUP "$CONTAINER"
echo "[clear_cache] SIGHUP sent to $CONTAINER. Cache flush triggered. SUCCESS."
exit 0
