#!/bin/bash
#
# Call POST /v1/graph/summarize: dense paragraph from an expanded subgraph.
# Usage: ./scripts/graph-summarize.sh [--instance=X] [-depth=N] [-limit=N] [-limit-per-edge=N] [-user-id=X] [-seeds=id1,id2] <query>
#
# By default, runs hybrid search for seeds (same as graph-query.sh), then summarizes.
# With -seeds=uuid1,uuid2,... search is skipped and those nodes are used as seeds.
#
# Examples:
#   ./scripts/graph-summarize.sh "What do we know about Alice?"
#   ./scripts/graph-summarize.sh --instance=prod -depth=3 -seeds=entity_abc...,entity_def... "trip planning"
#
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$REPO_ROOT/scripts/lib/env-confirm.sh"

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

DEPTH=2
LIMIT=10
LIMIT_PER_EDGE=5
USER_ID="global"
SEEDS_ARG=""
QUERY_PARTS=()

idx=0
while [ "$idx" -lt "${#args[@]}" ]; do
  arg="${args[$idx]}"
  case "$arg" in
    --instance=*|--instance)
      if [ "$arg" = "--instance" ]; then
        idx=$((idx + 1))
      fi
      ;;
    -depth=*|--depth=*)
      DEPTH="${arg#*=}"
      ;;
    -depth|--depth)
      idx=$((idx + 1))
      if [ "$idx" -ge "${#args[@]}" ]; then
        echo "Error: $arg requires a value"
        exit 1
      fi
      DEPTH="${args[$idx]}"
      ;;
    -limit=*|--limit=*)
      LIMIT="${arg#*=}"
      ;;
    -limit|--limit)
      idx=$((idx + 1))
      if [ "$idx" -ge "${#args[@]}" ]; then
        echo "Error: $arg requires a value"
        exit 1
      fi
      LIMIT="${args[$idx]}"
      ;;
    -limit-per-edge=*|--limit-per-edge=*)
      LIMIT_PER_EDGE="${arg#*=}"
      ;;
    -limit-per-edge|--limit-per-edge)
      idx=$((idx + 1))
      if [ "$idx" -ge "${#args[@]}" ]; then
        echo "Error: $arg requires a value"
        exit 1
      fi
      LIMIT_PER_EDGE="${args[$idx]}"
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
    -seeds=*|--seeds=*)
      SEEDS_ARG="${arg#*=}"
      ;;
    -seeds|--seeds)
      idx=$((idx + 1))
      if [ "$idx" -ge "${#args[@]}" ]; then
        echo "Error: $arg requires a value"
        exit 1
      fi
      SEEDS_ARG="${args[$idx]}"
      ;;
    *)
      QUERY_PARTS+=("$arg")
      ;;
  esac
  idx=$((idx + 1))
done

QUERY="${QUERY_PARTS[*]}"

if [ -z "$QUERY" ]; then
  echo "Usage: $0 [--instance=X] [-depth=N] [-limit=N] [-limit-per-edge=N] [-user-id=X] [-seeds=id1,id2,...] <query>"
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

AUTH_HEADER="Authorization: Bearer $(gcloud auth print-identity-token)"

if [ -n "$SEEDS_ARG" ]; then
  SEED_IDS=$(jq -n --arg s "$SEEDS_ARG" '$s | split(",") | map(gsub("^\\s+|\\s+$"; "")) | map(select(length > 0))')
else
  echo "Searching for seeds: \"$QUERY\" (limit=$LIMIT user_id=$USER_ID)"
  SEARCH_RESP=$(curl -s -X POST "$BASE_URL/v1/search" \
    -H "Content-Type: application/json" \
    -H "$AUTH_HEADER" \
    -d "{\"query\": $(echo "$QUERY" | jq -R .), \"limit\": $LIMIT, \"user_id\": \"$USER_ID\"}")

  if ! echo "$SEARCH_RESP" | jq -e . > /dev/null 2>&1; then
    echo "Error: search returned non-JSON:"
    echo "$SEARCH_RESP"
    exit 1
  fi

  if echo "$SEARCH_RESP" | jq -e '.detail' > /dev/null 2>&1; then
    echo "API error: $(echo "$SEARCH_RESP" | jq -r '.detail')"
    exit 1
  fi

  SEED_IDS=$(echo "$SEARCH_RESP" | jq -r '[.results[].uuid] | @json')
  if [ "$SEED_IDS" = "[]" ] || [ -z "$SEED_IDS" ]; then
    echo "No matching nodes found; nothing to summarize."
    exit 0
  fi
  echo "Seeds: $(echo "$SEED_IDS" | jq 'length') node(s)"
fi

echo ""
echo "Summarizing (depth=$DEPTH limit_per_edge=$LIMIT_PER_EDGE)..."

SUMMARY_RESP=$(curl -s -X POST "$BASE_URL/v1/graph/summarize" \
  -H "Content-Type: application/json" \
  -H "$AUTH_HEADER" \
  -d "{\"seed_ids\": $SEED_IDS, \"query\": $(echo "$QUERY" | jq -R .), \"hops\": $DEPTH, \"limit_per_edge\": $LIMIT_PER_EDGE, \"user_id\": \"$USER_ID\"}")

if ! echo "$SUMMARY_RESP" | jq -e . > /dev/null 2>&1; then
  echo "Error: summarize returned non-JSON:"
  echo "$SUMMARY_RESP"
  exit 1
fi

if echo "$SUMMARY_RESP" | jq -e '.detail' > /dev/null 2>&1; then
  echo "API error: $(echo "$SUMMARY_RESP" | jq -r '.detail')"
  exit 1
fi

echo ""
echo "$SUMMARY_RESP" | jq -r '.summary'
