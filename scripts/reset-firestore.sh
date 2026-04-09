#!/bin/bash
#
# Reset Firestore: delete all documents from collections used by Jot, then purge the Cloud Tasks queue.
# Usage: ./scripts/reset-firestore.sh <dev|prod>
# Environment must be explicit (no default). Script will confirm before continuing.
#
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
source "$REPO_ROOT/scripts/lib/env-confirm.sh"
require_env_and_confirm "$1"
shift

if [ -f "$ENV_FILE" ]; then
  echo "Targeting $ENV_TARGET (using $ENV_FILE)"
  set -a
  source "$ENV_FILE"
  set +a
else
  echo "Error: $ENV_FILE not found."
  exit 1
fi

go run ./cmd/admin reset-firestore

# Purge the Cloud Tasks queue so pending tasks (process-entry, process-sms-query, save-query, etc.) are cleared.
QUEUE_NAME="${CLOUD_TASKS_QUEUE:-jot-sync-queue}"
QUEUE_LOCATION="${CLOUD_TASKS_LOCATION:-us-central1}"
PROJECT="${GOOGLE_CLOUD_PROJECT:-}"
if [ -z "$PROJECT" ]; then
  echo "Warning: GOOGLE_CLOUD_PROJECT not set; skipping queue purge."
  exit 0
fi
echo "Purging Cloud Tasks queue: $QUEUE_NAME (location: $QUEUE_LOCATION)"
if gcloud tasks queues purge "$QUEUE_NAME" --location="$QUEUE_LOCATION" --project="$PROJECT" --quiet 2>/dev/null; then
  echo "Queue purged."
else
  echo "Warning: failed to purge queue (queue may not exist or gcloud not configured)."
fi
