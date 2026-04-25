#!/bin/bash
#
# Ingest a corpus of files into the Lethe graph.
# Usage: ./scripts/corpus.sh [--instance=X] [-domain=X] [-user-id=X] -corpus-id=<id> <file1> [file2] ...
#
# Examples:
#   ./scripts/corpus.sh -corpus-id="Project Zum" src/main.py src/utils.py
#   ./scripts/corpus.sh --instance=staging -corpus-id="lethe" -domain=code lethe/graph/*.py
#   ./scripts/corpus.sh -corpus-id="docs" -user-id=alice README.md docs/design.md
#
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$REPO_ROOT/scripts/lib/env-confirm.sh"

# Parse --instance before confirm
INSTANCE=""
args=("$@")
idx=0
while [ "$idx" -lt "${#args[@]}" ]; do
  arg="${args[$idx]}"
  case "$arg" in
    --instance=*)
      INSTANCE="${arg#*=}"
      ;;
    --instance)
      idx=$((idx + 1))
      if [ "$idx" -ge "${#args[@]}" ]; then
        echo "Error: --instance requires a value"
        exit 1
      fi
      INSTANCE="${args[$idx]}"
      ;;
  esac
  idx=$((idx + 1))
done

require_env_and_confirm "$INSTANCE"

if [ -f "$ENV_FILE" ]; then set -a; source "$ENV_FILE"; set +a; fi

PROJECT="${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT in $ENV_FILE}"
REGION="${LETHE_REGION:-us-central1}"
SERVICE_NAME="lethe-api"

# Parse flags and collect file paths
CORPUS_ID=""
DOMAIN=""
USER_ID="global"
FILES=()

idx=0
while [ "$idx" -lt "${#args[@]}" ]; do
  arg="${args[$idx]}"
  case "$arg" in
    --instance=*|--instance)
      if [ "$arg" = "--instance" ]; then
        idx=$((idx + 1))
      fi
      ;;
    -corpus-id=*|--corpus-id=*)
      CORPUS_ID="${arg#*=}"
      ;;
    -corpus-id|--corpus-id)
      idx=$((idx + 1))
      if [ "$idx" -ge "${#args[@]}" ]; then
        echo "Error: $arg requires a value"
        exit 1
      fi
      CORPUS_ID="${args[$idx]}"
      ;;
    -domain=*|--domain=*)
      DOMAIN="${arg#*=}"
      ;;
    -domain|--domain)
      idx=$((idx + 1))
      if [ "$idx" -ge "${#args[@]}" ]; then
        echo "Error: $arg requires a value"
        exit 1
      fi
      DOMAIN="${args[$idx]}"
      ;;
    -user-id=*|--user-id=*)
      USER_ID="${arg#*=}"
      ;;
    -user-id|--user-id)
      idx=$((idx + 1))
      if [ "$idx" -ge "${#args[@]}" ]; then
        echo "Error: $arg requires a value"
        exit 1
      fi
      USER_ID="${args[$idx]}"
      ;;
    *)
      FILES+=("$arg")
      ;;
  esac
  idx=$((idx + 1))
done

USAGE="Usage: $0 [--instance=X] [-domain=X] [-user-id=X] -corpus-id=<id> <file1> [file2] ..."

if [ -z "$CORPUS_ID" ]; then
  echo "Error: -corpus-id is required"
  echo "$USAGE"
  exit 1
fi

if [ "${#FILES[@]}" -eq 0 ]; then
  echo "Error: at least one file path is required"
  echo "$USAGE"
  exit 1
fi

# Validate files exist
for filepath in "${FILES[@]}"; do
  if [ ! -f "$filepath" ]; then
    echo "Error: file not found: $filepath"
    exit 1
  fi
done

if ! command -v curl &>/dev/null; then echo "Error: curl required"; exit 1; fi
if ! command -v jq &>/dev/null; then echo "Error: jq required"; exit 1; fi

BASE_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --region="$REGION" --project="$PROJECT" --format='value(status.url)' 2>/dev/null)

if [ -z "$BASE_URL" ]; then
  echo "Error: could not determine Lethe service URL. Deploy first."
  exit 1
fi

AUTH_TOKEN=$(gcloud auth print-identity-token)

# Build documents array
DOCS_JSON="[]"
for filepath in "${FILES[@]}"; do
  filename=$(basename "$filepath")
  if [ ! -s "$filepath" ]; then
    echo "Skipping empty file: $filename" >&2
    continue
  fi
  file_content=$(jq -Rs '.' < "$filepath")
  DOCS_JSON=$(echo "$DOCS_JSON" | jq \
    --arg filename "$filename" \
    --argjson text "$file_content" \
    '. + [{"text": $text, "filename": $filename}]')
done

if [ "$(echo "$DOCS_JSON" | jq 'length')" -eq 0 ]; then
  echo "Error: no non-empty files to ingest"
  exit 1
fi

# Build final payload
PAYLOAD=$(jq -n \
  --arg corpus_id "$CORPUS_ID" \
  --arg user_id "$USER_ID" \
  --arg domain "$DOMAIN" \
  --argjson documents "$DOCS_JSON" \
  '{
    corpus_id: $corpus_id,
    user_id: $user_id,
    documents: $documents
  }
  | if $domain != "" then . + {domain: $domain} else . end
')

# Build a comma-separated list of filenames for display
FILE_NAMES=$(printf '%s\n' "${FILES[@]}" | xargs -I{} basename {} | paste -sd ', ' -)
FILE_COUNT="${#FILES[@]}"

echo ""
echo "Corpus: $CORPUS_ID"
echo "Files:  $FILE_NAMES ($FILE_COUNT files)"
[ -n "$DOMAIN" ] && echo "Domain: $DOMAIN"
echo "User:   $USER_ID"
echo "Ingesting into: $BASE_URL"
echo ""

RESPONSE=$(curl -s -X POST "$BASE_URL/v1/ingest/corpus" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $AUTH_TOKEN" \
  -d "$PAYLOAD")

echo "$RESPONSE" | jq '{
  corpus_id,
  document_ids_count: (.document_ids | length),
  total_chunks,
  nodes_created: (.nodes_created | length),
  nodes_updated: (.nodes_updated | length),
  relationships_created: (.relationships_created | length)
}'
