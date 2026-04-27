#!/bin/bash
#
# Print node and edge statistics for the Lethe graph database.
# Usage: ./scripts/stats.sh [--instance=X]
#
# Examples:
#   ./scripts/stats.sh
#   ./scripts/stats.sh --instance=staging
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

if ! command -v curl &>/dev/null; then echo "Error: curl required"; exit 1; fi
if ! command -v jq &>/dev/null; then echo "Error: jq required"; exit 1; fi

BASE_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --region="$REGION" --project="$PROJECT" --format='value(status.url)' 2>/dev/null)

if [ -z "$BASE_URL" ]; then
  echo "Error: could not determine Lethe service URL. Deploy first."
  exit 1
fi

AUTH_TOKEN=$(gcloud auth print-identity-token)

STATS_STATUS=$(curl -s -o /tmp/lethe_stats.json -w "%{http_code}" \
  "$BASE_URL/v1/stats" \
  -H "Authorization: Bearer $AUTH_TOKEN")

if [ "$STATS_STATUS" != "200" ]; then
  echo "Error: /v1/stats returned HTTP $STATS_STATUS"
  cat /tmp/lethe_stats.json
  exit 1
fi

TYPES_STATUS=$(curl -s -o /tmp/lethe_node_types.json -w "%{http_code}" \
  "$BASE_URL/v1/node-types" \
  -H "Authorization: Bearer $AUTH_TOKEN")

if [ "$TYPES_STATUS" != "200" ]; then
  echo "Error: /v1/node-types returned HTTP $TYPES_STATUS"
  cat /tmp/lethe_node_types.json
  exit 1
fi

NODES_TOTAL=$(jq '.nodes_total' /tmp/lethe_stats.json)
EDGES_TOTAL=$(jq '.edges_total' /tmp/lethe_stats.json)

echo ""
echo "Instance: ${INSTANCE:-default}  Project: $PROJECT"
echo ""
printf "Nodes  %d total\n" "$NODES_TOTAL"
echo "---------------------------------------"
jq -r '.nodes_by_type | to_entries | sort_by(-.value) | .[] | [.key, .value] | @tsv' \
  /tmp/lethe_stats.json | awk -F'\t' '{printf "  %-28s %d\n", $1, $2}'
echo ""
printf "Edges  %d total\n" "$EDGES_TOTAL"
echo "---------------------------------------"
jq -r '.edges_by_predicate | to_entries | sort_by(-.value) | .[] | [.key, .value] | @tsv' \
  /tmp/lethe_stats.json | awk -F'\t' '{printf "  %-28s %d\n", $1, $2}'
echo ""
echo "Canonical node types"
echo "---------------------------------------"
jq -r '.node_types[]' /tmp/lethe_node_types.json | sort | awk '{printf "  %s\n", $0}'
echo ""
echo "Canonical predicates"
echo "---------------------------------------"
jq -r '.allowed_predicates[]' /tmp/lethe_node_types.json | sort | awk '{printf "  %s\n", $0}'
echo ""
