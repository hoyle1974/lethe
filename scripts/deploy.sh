#!/bin/bash
#
# Build and deploy Lethe to Cloud Run.
# Usage: ./scripts/deploy.sh <dev|prod>
#
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
source "$REPO_ROOT/scripts/lib/env-confirm.sh"
require_env_and_confirm "${1:-}"

if [ -f "$ENV_FILE" ]; then
  set -a; source "$ENV_FILE"; set +a
else
  echo "Error: $ENV_FILE not found."; exit 1
fi

PROJECT="${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT in $ENV_FILE}"
REGION="${LETHE_REGION:-us-central1}"
SERVICE_NAME="lethe-api"
IMAGE="$REGION-docker.pkg.dev/$PROJECT/lethe/$SERVICE_NAME"
COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "dev")
IMAGE_TAG="sha-$COMMIT"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

echo "Project: $PROJECT  Region: $REGION  Image: $IMAGE:$IMAGE_TAG"

if ! command -v gcloud &>/dev/null; then
  echo -e "${RED}Error: gcloud CLI not found${NC}"; exit 1
fi
gcloud config set project "$PROJECT" 2>/dev/null

echo -e "${YELLOW}Applying Firestore indexes...${NC}"
if ! command -v firebase &>/dev/null; then
  echo -e "${RED}Error: firebase CLI not found. Install it to deploy firestore.indexes.json.${NC}"
  exit 1
fi
firebase use "$PROJECT" --non-interactive >/dev/null 2>&1 || true
firebase deploy --only firestore:indexes --project "$PROJECT" --non-interactive

echo -e "${YELLOW}Running tests...${NC}"
if [ -f "$REPO_ROOT/.venv/bin/pip" ]; then
  PYTHON="$REPO_ROOT/.venv/bin/python"
  PIP="$REPO_ROOT/.venv/bin/pip"
  PYTEST="$REPO_ROOT/.venv/bin/pytest"
else
  PYTHON=python3
  PIP=pip
  PYTEST=pytest
fi
"$PIP" install -r requirements-dev.txt -q
"$PYTEST" tests/ -q

echo -e "${YELLOW}Authenticating Docker with Artifact Registry...${NC}"
gcloud auth configure-docker "$REGION-docker.pkg.dev" --quiet

echo -e "${YELLOW}Building container ($IMAGE_TAG)...${NC}"
docker build --platform linux/amd64 --progress=plain \
  -t "$IMAGE:$IMAGE_TAG" \
  -t "$IMAGE:latest" \
  .

echo -e "${YELLOW}Pushing $IMAGE_TAG...${NC}"
docker push "$IMAGE:$IMAGE_TAG"
echo -e "${YELLOW}Pushing latest...${NC}"
docker push "$IMAGE:latest"

echo -e "${YELLOW}Deploying to Cloud Run...${NC}"
ENV_VARS="GOOGLE_CLOUD_PROJECT=$PROJECT"
ENV_VARS="$ENV_VARS,LETHE_COLLECTION=${LETHE_COLLECTION:-nodes}"
ENV_VARS="$ENV_VARS,LETHE_EMBEDDING_MODEL=${LETHE_EMBEDDING_MODEL:-text-embedding-005}"
ENV_VARS="$ENV_VARS,LETHE_LLM_MODEL=${LETHE_LLM_MODEL:-gemini-2.5-flash}"
ENV_VARS="$ENV_VARS,LETHE_COLLISION_DETECTION=${LETHE_COLLISION_DETECTION:-true}"
ENV_VARS="$ENV_VARS,LETHE_RRF_K=${LETHE_RRF_K:-60}"
ENV_VARS="$ENV_VARS,LETHE_REGION=$REGION"
ENV_VARS="$ENV_VARS,LOG_LEVEL=${LOG_LEVEL:-info}"

gcloud run deploy "$SERVICE_NAME" \
  --image="$IMAGE:$IMAGE_TAG" \
  --region="$REGION" \
  --platform=managed \
  --no-allow-unauthenticated \
  --memory=512Mi \
  --cpu=1 \
  --concurrency=80 \
  --set-env-vars="$ENV_VARS" \
  --quiet

DEPLOYED_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --region="$REGION" --format='value(status.url)')

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Lethe deployed: $DEPLOYED_URL${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
