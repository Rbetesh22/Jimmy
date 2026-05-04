#!/bin/bash
set -euo pipefail

# ── Config ───────────────────────────────────────────────────────────────────
PROJECT_ID="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${GCP_REGION:-us-east1}"
SERVICE="jimmy"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE}"

if [[ -z "$PROJECT_ID" ]]; then
  echo "ERROR: Set GCP_PROJECT env var or run: gcloud config set project YOUR_PROJECT_ID"
  exit 1
fi

SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "latest")
TAGGED="${IMAGE}:${SHA}"

echo "Project:  $PROJECT_ID"
echo "Region:   $REGION"
echo "Image:    $TAGGED"
echo ""

# ── Build & Push ─────────────────────────────────────────────────────────────
echo "Building..."
docker build -t "$TAGGED" -t "${IMAGE}:latest" .

echo "Pushing..."
docker push "$TAGGED"
docker push "${IMAGE}:latest"

# ── Deploy ───────────────────────────────────────────────────────────────────
echo "Deploying to Cloud Run..."
gcloud run deploy "$SERVICE" \
  --image="$TAGGED" \
  --region="$REGION" \
  --platform=managed \
  --allow-unauthenticated \
  --port=7700 \
  --memory=4Gi \
  --cpu=2 \
  --min-instances=1 \
  --max-instances=10 \
  --timeout=300 \
  --concurrency=80 \
  --set-env-vars="JIMMY_DATA_DIR=/data" \
  --add-volume=name=jimmy-data,type=cloud-storage,bucket="${PROJECT_ID}-jimmy-data" \
  --add-volume-mount=volume=jimmy-data,mount-path=/data

echo ""
URL=$(gcloud run services describe "$SERVICE" --region="$REGION" --format='value(status.url)')
echo "Live at: $URL"

# ── One-time setup reminder ───────────────────────────────────────────────────
# Run once before first deploy:
#
#   gcloud services enable run.googleapis.com containerregistry.googleapis.com storage.googleapis.com
#
#   gcloud storage buckets create gs://${PROJECT_ID}-jimmy-data --location=${REGION}
#
#   # Grant Cloud Run SA access to the bucket:
#   SA="$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
#   gcloud storage buckets add-iam-policy-binding gs://${PROJECT_ID}-jimmy-data \
#     --member="serviceAccount:${SA}" --role="roles/storage.objectAdmin"
#
#   # Set secrets (run after first deploy):
#   gcloud run services update jimmy --region=${REGION} \
#     --set-env-vars="ANTHROPIC_API_KEY=xxx,OPENAI_API_KEY=yyy,..."
