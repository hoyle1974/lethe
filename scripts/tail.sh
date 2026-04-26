#!/bin/bash
#
# Tail Cloud Run logs for Lethe.
# Usage: ./scripts/tail.sh [--instance=X] [--lines=N]
#
# Shows the last N log lines on startup (default 20), then streams live.
#
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$REPO_ROOT/scripts/lib/env-confirm.sh"

INSTANCE=""
LINES=20
for arg in "$@"; do
  case "$arg" in
    --instance=*) INSTANCE="${arg#*=}" ;;
    --lines=*)    LINES="${arg#*=}" ;;
  esac
done

require_env_and_confirm "$INSTANCE"

if [ -f "$ENV_FILE" ]; then set -a; source "$ENV_FILE"; set +a; fi

PROJECT="${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT in $ENV_FILE}"
SERVICE_NAME="lethe-api"
REGION="${LETHE_REGION:-us-central1}"

LOG_FILTER="resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$SERVICE_NAME\""

echo "Streaming logs: $SERVICE_NAME (project=$PROJECT region=$REGION)"
echo "Ctrl+C to stop."
echo ""

# Show recent history before handing off to the live tail
echo "--- last $LINES lines ---"
gcloud logging read "$LOG_FILTER" \
  --project="$PROJECT" \
  --limit="$LINES" \
  --order=asc \
  --format="value(timestamp, textPayload)"
echo "--- live ---"

exec gcloud beta logging tail "$LOG_FILTER" \
  --project="$PROJECT" \
  --buffer-window=2s \
  --format="value(timestamp, textPayload)"
