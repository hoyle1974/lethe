#!/bin/bash
#
# Query the Lethe graph and render it in the terminal.
# Usage: ./scripts/graph-query.sh [--instance=X] [-depth=N] [-limit=N] [-limit-per-edge=N] [-user-id=X] <query>
#
# Examples:
#   ./scripts/graph-query.sh "Alice Acme"
#   ./scripts/graph-query.sh --instance=staging "work projects"
#   ./scripts/graph-query.sh -depth=3 -limit=5 -user-id=jot "engineering"
#
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$REPO_ROOT/scripts/lib/env-confirm.sh"

# Parse --instance= before confirm (everything else parsed after)
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

# Parse remaining flags
DEPTH=2
LIMIT=10
LIMIT_PER_EDGE=5
USER_ID="global"
QUERY=""

for arg in "$@"; do
  case "$arg" in
    --instance=*)      ;;  # already handled
    -depth=*)          DEPTH="${arg#*=}" ;;
    -limit=*)          LIMIT="${arg#*=}" ;;
    -limit-per-edge=*) LIMIT_PER_EDGE="${arg#*=}" ;;
    -user-id=*)        USER_ID="${arg#*=}" ;;
    *)                 QUERY="$arg" ;;
  esac
done

if [ -z "$QUERY" ]; then
  echo "Usage: $0 [--instance=X] [-depth=N] [-limit=N] [-limit-per-edge=N] [-user-id=X] <query>"
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

echo "Querying: \"$QUERY\" (depth=$DEPTH limit=$LIMIT user_id=$USER_ID)"
echo ""

# Step 1: hybrid search to find seeds
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

echo "Search response: $(echo "$SEARCH_RESP" | jq '{count, results: [.results[]? | {uuid, node_type, content: .content[0:60]}]}')"
echo ""

SEED_IDS=$(echo "$SEARCH_RESP" | jq -r '[.results[].uuid] | @json')

if [ "$SEED_IDS" = "[]" ] || [ -z "$SEED_IDS" ]; then
  echo "No matching nodes found."
  exit 0
fi

echo "Seeds found: $(echo "$SEED_IDS" | jq 'length')"

# Step 2: graph expand
EXPAND_RESP=$(curl -s -X POST "$BASE_URL/v1/graph/expand" \
  -H "Content-Type: application/json" \
  -H "$AUTH_HEADER" \
  -d "{\"seed_ids\": $SEED_IDS, \"query\": $(echo "$QUERY" | jq -R .), \"hops\": $DEPTH, \"limit_per_edge\": $LIMIT_PER_EDGE, \"user_id\": \"$USER_ID\"}")

DOT_FILE="/tmp/lethe-graph-query.dot"
PNG_FILE="/tmp/lethe-graph-query.png"

# Generate DOT from response
echo "$EXPAND_RESP" | jq -r '
  "digraph lethe {",
  "  rankdir=LR;",
  "  node [shape=box fontname=Helvetica fontsize=10];",
  "  edge [fontname=Helvetica fontsize=9];",
  (
    .nodes | to_entries[] |
    "  \"" + .key + "\" [label=\"" +
      (.value.node_type | ascii_downcase) + "\n" +
      (.value.content | .[0:40] | gsub("\""; "'"'"'")) +
    "\"];"
  ),
  (
    .edges[] |
    "  \"" + .subject + "\" -> \"" + .object + "\" [label=\"" + .predicate + "\"];"
  ),
  "}"
' > "$DOT_FILE"

echo ""

# Render if tools available, otherwise plain text
if [ -s "$DOT_FILE" ] && command -v dot &>/dev/null && command -v imgcat &>/dev/null; then
  dot -Tpng "$DOT_FILE" -o "$PNG_FILE" 2>/dev/null
  imgcat --width "$(tput cols)" "$PNG_FILE"
  echo "(graph saved to $PNG_FILE)"
else
  echo "=== Nodes ==="
  echo "$EXPAND_RESP" | jq -r '.nodes | to_entries[] | "[\(.value.node_type)] \(.value.content | .[0:80])"'
  echo ""
  echo "=== Edges ==="
  echo "$EXPAND_RESP" | jq -r '.edges[] | "\(.subject[0:8]) --[\(.predicate)]--> \(.object[0:8])"'
  if ! command -v dot &>/dev/null; then
    echo ""
    echo "(Install graphviz and imgcat for visual rendering)"
  fi
fi
