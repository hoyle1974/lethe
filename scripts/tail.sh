#!/bin/bash
#
# Tail Cloud Run logs for Lethe.
# Usage: ./scripts/tail.sh [--instance=X]
#
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$REPO_ROOT/scripts/lib/env-confirm.sh"

INSTANCE=""
for arg in "$@"; do
  case "$arg" in
    --instance=*) INSTANCE="${arg#*=}" ;;
  esac
done

require_env_and_confirm "$INSTANCE"

if [ -f "$ENV_FILE" ]; then set -a; source "$ENV_FILE"; set +a; fi

PROJECT="${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT in $ENV_FILE}"
SERVICE_NAME="lethe-api"
REGION="${LETHE_REGION:-us-central1}"

echo "Streaming logs: $SERVICE_NAME (project=$PROJECT region=$REGION)"
echo "Ctrl+C to stop."
echo ""

gcloud beta run services logs tail "$SERVICE_NAME" \
  --region="$REGION" \
  --project="$PROJECT"
