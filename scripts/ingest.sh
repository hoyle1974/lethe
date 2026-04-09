#!/bin/bash
#
# Ingest text into the Lethe graph.
# Usage: ./scripts/ingest.sh [--instance=X] [-domain=X] [-user-id=X] [-source=X] <text>
#
# Examples:
#   ./scripts/ingest.sh "Alice works at Acme Corp"
#   ./scripts/ingest.sh --instance=staging "Bob lives in Paris"
#   ./scripts/ingest.sh -domain=work -user-id=jot "Alice started the new project today"
#   ./scripts/ingest.sh --instance=staging -domain=personal "Went hiking this weekend"
#
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$REPO_ROOT/scripts/lib/env-confirm.sh"

# Parse --instance= before confirm
INSTANCE=""
for arg in "$@"; do
  case "$arg" in
    --instance=*) INSTANCE="${arg#*=}" ;;
  esac
done

require_env_and_confirm "$INSTANCE"

if [ -f "$ENV_FILE" ]; then set -a; source "$ENV_FILE"; set +a; fi

PROJECT="${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT in $ENV_FILE}"
REGION="${LETHE_REGION:-us-central1}"
SERVICE_NAME="lethe-api"

# Parse flags and collect text
DOMAIN=""
USER_ID="global"
SOURCE=""
TEXT=""

for arg in "$@"; do
  case "$arg" in
    --instance=*) ;;  # already handled
    -domain=*)    DOMAIN="${arg#*=}" ;;
    -user-id=*)   USER_ID="${arg#*=}" ;;
    -source=*)    SOURCE="${arg#*=}" ;;
    *)            TEXT="$arg" ;;
  esac
done

if [ -z "$TEXT" ]; then
  echo "Usage: $0 [instance] [-domain=X] [-user-id=X] [-source=X] <text>"
  exit 1
fi

if ! command -v curl &>/dev/null; then echo "Error: curl required"; exit 1; fi
if ! command -v jq &>/dev/null; then echo "Error: jq required"; exit 1; fi

BASE_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --region="$REGION" --project="$PROJECT" --format='value(status.url)' 2>/dev/null)

if [ -z "$BASE_URL" ]; then
  echo "Error: could not determine Lethe service URL. Deploy first."
  exit 1
fi

AUTH_TOKEN=$(gcloud auth print-identity-token)

# Build JSON payload
PAYLOAD=$(jq -n \
  --arg text "$TEXT" \
  --arg domain "$DOMAIN" \
  --arg user_id "$USER_ID" \
  --arg source "$SOURCE" \
  '{
    text: $text,
    user_id: $user_id
  }
  | if $domain != "" then . + {domain: $domain} else . end
  | if $source != "" then . + {source: $source} else . end
')

echo ""
echo "Ingesting into: $BASE_URL"
echo "Text: $TEXT"
[ -n "$DOMAIN" ] && echo "Domain: $DOMAIN"
[ -n "$SOURCE" ] && echo "Source: $SOURCE"
echo "User:  $USER_ID"
echo ""

RESPONSE=$(curl -s -X POST "$BASE_URL/v1/ingest" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $AUTH_TOKEN" \
  -d "$PAYLOAD")

echo "$RESPONSE" | jq '{
  entry_uuid,
  nodes_created: (.nodes_created | length),
  nodes_updated: (.nodes_updated | length),
  relationships_created: (.relationships_created | length),
  node_ids: .nodes_created,
  relationship_ids: .relationships_created
}'
