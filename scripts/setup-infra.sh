#!/bin/bash
#
# Lethe infrastructure setup — run once per new project deployment.
# Usage: ./scripts/setup-infra.sh <dev|prod>
#
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$REPO_ROOT/scripts/lib/env-confirm.sh"
require_env_and_confirm "${1:-}"

if [ -f "$ENV_FILE" ]; then
  set -a; source "$ENV_FILE"; set +a
else
  echo "Error: $ENV_FILE not found. Copy .env.example and fill in values."
  exit 1
fi

PROJECT="${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT in $ENV_FILE}"
REGION="${LETHE_REGION:-us-central1}"
SERVICE_NAME="lethe-api"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

echo -e "${YELLOW}Lethe Infrastructure Setup${NC}"
echo "Project: $PROJECT  Region: $REGION"
echo ""

if ! command -v gcloud &>/dev/null; then
  echo -e "${RED}Error: gcloud CLI not found${NC}"; exit 1
fi
gcloud config set project "$PROJECT" 2>/dev/null

echo -e "${CYAN}Enabling required APIs...${NC}"
gcloud services enable \
  run.googleapis.com \
  firestore.googleapis.com \
  aiplatform.googleapis.com \
  artifactregistry.googleapis.com \
  --quiet
echo -e "${GREEN}APIs enabled${NC}"

echo -e "${CYAN}Setting up Artifact Registry...${NC}"
REPO_NAME="lethe"
if gcloud artifacts repositories describe "$REPO_NAME" --location="$REGION" 2>/dev/null; then
  echo -e "${YELLOW}Repository $REPO_NAME already exists${NC}"
else
  gcloud artifacts repositories create "$REPO_NAME" \
    --repository-format=docker \
    --location="$REGION" \
    --description="Lethe Docker repository" \
    --quiet
  echo -e "${GREEN}Repository $REPO_NAME created${NC}"
fi

echo -e "${CYAN}Initializing Firestore (native mode)...${NC}"
gcloud firestore databases create \
  --location="$REGION" \
  --type=firestore-native \
  2>/dev/null || echo -e "${YELLOW}Firestore already initialized${NC}"

echo -e "${CYAN}Creating Firestore vector index (embedding field)...${NC}"
gcloud firestore indexes composite create \
  --project="$PROJECT" \
  --collection-group=nodes \
  --query-scope=COLLECTION \
  --field-config=order=ASCENDING,field-path=user_id \
  --field-config='vector-config={"dimension":"768","flat": "{}"},field-path=embedding' \
  --quiet 2>/dev/null \
  && echo -e "${GREEN}Vector index created (building in background — may take a few minutes)${NC}" \
  || echo -e "${YELLOW}Vector index already exists or creation failed — check Firebase Console${NC}"

echo -e "${CYAN}Deploying Firestore composite indexes...${NC}"
if command -v firebase &>/dev/null; then
  firebase deploy --only firestore:indexes --project "$PROJECT" --non-interactive --force
else
  echo -e "${YELLOW}Firebase CLI not found — deploy indexes manually with: firebase deploy --only firestore:indexes --project $PROJECT${NC}"
fi

echo ""
echo -e "${GREEN}Infrastructure setup complete!${NC}"
echo "Next: ./scripts/deploy.sh${ENV_TARGET:+ $ENV_TARGET}"
