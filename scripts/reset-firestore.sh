#!/bin/bash
#
# Reset Firestore: delete all documents from the Lethe collection.
# Usage: ./scripts/reset-firestore.sh [dev|prod]
# Environment must be explicit (no default). Script will confirm before continuing.
#
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
source "$REPO_ROOT/scripts/lib/env-confirm.sh"
require_env_and_confirm "$1"

# SAFE SHIFT: Only shift if an argument was actually provided
[[ $# -gt 0 ]] && shift || true

if [ -f "$ENV_FILE" ]; then
  set -a; source "$ENV_FILE"; set +a
else
  echo "Error: $ENV_FILE not found."
  exit 1
fi

PROJECT="${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT in $ENV_FILE}"
COLLECTION="${LETHE_COLLECTION:-nodes}"

echo ""
echo -e "\033[0;31mWARNING: This will permanently delete all data in '$COLLECTION'.\033[0m"
echo "Project: $PROJECT"

if [ "${LETHE_SKIP_CONFIRM:-0}" != "1" ]; then
  read -r -p "Are you absolutely sure? [y/N] " confirm_delete
  case "$confirm_delete" in
    [yY][eE][sS]|[yY]) ;;
    *) echo "Aborted."; exit 1 ;;
  esac
fi

if ! command -v firebase &>/dev/null; then
  echo "Error: firebase CLI is required to delete collections but was not found."
  echo "Please install it: npm install -g firebase-tools"
  exit 1
fi

echo "Wiping Firestore collection: $COLLECTION..."

# -r means recursive (handles subcollections if any ever get created)
# --force bypasses the secondary interactive prompt from the CLI itself
firebase firestore:delete "$COLLECTION" --project "$PROJECT" -r --force

echo ""
echo "Firestore reset complete. Ready for test.sh!"
