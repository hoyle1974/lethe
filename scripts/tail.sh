#!/bin/bash
#
# Tail Cloud Run logs for Lethe.
# Usage: ./scripts/tail.sh <dev|prod>
#
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$REPO_ROOT/scripts/lib/env-confirm.sh"
require_env_and_confirm "${1:-}"

if [ -f "$ENV_FILE" ]; then set -a; source "$ENV_FILE"; set +a; fi

PROJECT="${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT in $ENV_FILE}"
SERVICE_NAME="lethe-api"
REGION="${LETHE_REGION:-us-central1}"
POLL_SEC="${LOG_TAIL_POLL_SEC:-2}"

FILTER="resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$SERVICE_NAME\" AND resource.labels.location=\"$REGION\""
last_ts=$(date -u +"%Y-%m-%dT%H:%M:%S.000000000Z")

echo "Tailing logs: $SERVICE_NAME (project=$PROJECT region=$REGION) from now, poll every ${POLL_SEC}s"
echo "Ctrl+C to stop."

use_jq=0; command -v jq &>/dev/null && use_jq=1

while true; do
  CURRENT_FILTER="$FILTER AND timestamp>\"$last_ts\""
  if [ "$use_jq" = 1 ]; then
    raw=$(gcloud logging read "$CURRENT_FILTER" \
      --project="$PROJECT" --limit=200 --order=asc --format=json 2>/dev/null) || true
    [ -z "$raw" ] && { sleep "$POLL_SEC"; continue; }
    echo "$raw" | jq -r '.[] |
      if .jsonPayload then
        (.jsonPayload.level // "INFO") as $l |
        (.jsonPayload.msg // .jsonPayload.message // "") as $m |
        if $m != "" then "\(.timestamp) [\($l)] \($m)" else empty end
      else
        (.textPayload // "") as $t |
        if $t == "" then empty else "\(.timestamp) \($t)" end
      end' 2>/dev/null || true
    latest=$(echo "$raw" | jq -r 'if length > 0 then .[-1].timestamp else empty end' 2>/dev/null)
    [ -n "$latest" ] && last_ts="$latest"
  else
    gcloud logging read "$CURRENT_FILTER" --project="$PROJECT" --limit=50 --order=asc \
      --format='table(timestamp,jsonPayload.level,jsonPayload.msg)' 2>/dev/null || true
    latest=$(gcloud logging read "$CURRENT_FILTER" --project="$PROJECT" --limit=1 \
      --order=desc --format='value(timestamp)' 2>/dev/null) || true
    [ -n "$latest" ] && last_ts="$latest"
  fi
  sleep "$POLL_SEC"
done
