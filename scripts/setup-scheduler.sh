#!/bin/bash
#
# Configure Cloud Scheduler sleep-cycle job for consolidation.
# Usage: ./scripts/setup-scheduler.sh <dev|prod>
#
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$REPO_ROOT/scripts/lib/env-confirm.sh"
require_env_and_confirm "${1:-}"

if [ -f "$ENV_FILE" ]; then
  set -a; source "$ENV_FILE"; set +a
else
  echo "Error: $ENV_FILE not found."; exit 1
fi

PROJECT="${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT in $ENV_FILE}"
REGION="${LETHE_REGION:-us-central1}"
SERVICE_NAME="${LETHE_SERVICE_NAME:-lethe-api}"
SCHEDULER_LOCATION="${LETHE_SCHEDULER_LOCATION:-$REGION}"
SCHEDULE="${LETHE_CONSOLIDATE_SCHEDULE:-0 2 * * *}"
TIME_ZONE="${LETHE_CONSOLIDATE_TIME_ZONE:-Etc/UTC}"
JOB_NAME="${LETHE_CONSOLIDATE_JOB_NAME:-lethe-consolidate-nightly}"
TARGET_USER_ID="${LETHE_CONSOLIDATE_USER_ID:-global}"
SA_NAME="${LETHE_SCHEDULER_SA_NAME:-lethe-scheduler-invoker}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

if ! command -v gcloud &>/dev/null; then
  echo -e "${RED}Error: gcloud CLI not found${NC}"; exit 1
fi

echo "Project: $PROJECT  Region: $REGION  Service: $SERVICE_NAME"
gcloud config set project "$PROJECT" 2>/dev/null

echo -e "${CYAN}Enabling Scheduler APIs...${NC}"
gcloud services enable \
  cloudscheduler.googleapis.com \
  iamcredentials.googleapis.com \
  --quiet

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
SA_EMAIL="$SA_NAME@$PROJECT.iam.gserviceaccount.com"
SCHEDULER_AGENT="service-$PROJECT_NUMBER@gcp-sa-cloudscheduler.iam.gserviceaccount.com"

if gcloud iam service-accounts describe "$SA_EMAIL" >/dev/null 2>&1; then
  echo -e "${YELLOW}Service account already exists: $SA_EMAIL${NC}"
else
  echo -e "${CYAN}Creating service account: $SA_NAME${NC}"
  gcloud iam service-accounts create "$SA_NAME" \
    --display-name="Lethe Scheduler Invoker" \
    --quiet
fi

echo -e "${CYAN}Granting Cloud Run invoker role...${NC}"
gcloud run services add-iam-policy-binding "$SERVICE_NAME" \
  --region="$REGION" \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/run.invoker" \
  --quiet

echo -e "${CYAN}Allowing Cloud Scheduler to mint OIDC tokens...${NC}"
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --member="serviceAccount:$SCHEDULER_AGENT" \
  --role="roles/iam.serviceAccountTokenCreator" \
  --quiet

SERVICE_URL="$(gcloud run services describe "$SERVICE_NAME" --region="$REGION" --format='value(status.url)')"
TARGET_URL="$SERVICE_URL/v1/admin/consolidate"
MESSAGE_BODY="{\"user_id\":\"$TARGET_USER_ID\"}"

if gcloud scheduler jobs describe "$JOB_NAME" --location="$SCHEDULER_LOCATION" >/dev/null 2>&1; then
  echo -e "${YELLOW}Updating existing scheduler job: $JOB_NAME${NC}"
  gcloud scheduler jobs update http "$JOB_NAME" \
    --location="$SCHEDULER_LOCATION" \
    --schedule="$SCHEDULE" \
    --time-zone="$TIME_ZONE" \
    --uri="$TARGET_URL" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body="$MESSAGE_BODY" \
    --oidc-service-account-email="$SA_EMAIL" \
    --oidc-token-audience="$SERVICE_URL" \
    --quiet
else
  echo -e "${CYAN}Creating scheduler job: $JOB_NAME${NC}"
  gcloud scheduler jobs create http "$JOB_NAME" \
    --location="$SCHEDULER_LOCATION" \
    --schedule="$SCHEDULE" \
    --time-zone="$TIME_ZONE" \
    --uri="$TARGET_URL" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body="$MESSAGE_BODY" \
    --oidc-service-account-email="$SA_EMAIL" \
    --oidc-token-audience="$SERVICE_URL" \
    --quiet
fi

echo ""
echo -e "${GREEN}Scheduler configured.${NC}"
echo "Job: $JOB_NAME"
echo "Schedule: $SCHEDULE ($TIME_ZONE)"
echo "URL: $TARGET_URL"
echo "OIDC Service Account: $SA_EMAIL"
