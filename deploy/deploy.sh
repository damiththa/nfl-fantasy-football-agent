#!/usr/bin/env bash
# =============================================================================
# NFL Fantasy Football Agent — Cloud Run Deployment Script
# =============================================================================
# Builds the container image in Google Cloud Build (no local Docker required)
# and deploys to Cloud Run with Secret Manager environment bindings.
# =============================================================================

set -euo pipefail

GCLOUD="/Users/madushan/google-cloud-sdk/bin/gcloud"
PROJECT_ID="gen-lang-client-0581555372"
PROJECT_NUMBER="652912521571"
SERVICE_NAME="fantasy-agent"

# Preferred green region: us-central1 (90%+ clean wind energy) or us-east4 (Low-CO2 East Coast)
REGION="${1:-us-central1}"

echo "============================================================"
echo "  Deploying ${SERVICE_NAME} to Google Cloud Run"
echo "  Project: ${PROJECT_ID} (${PROJECT_NUMBER})"
echo "  Region:  ${REGION}"
echo "============================================================"

# 1. Ensure required Google Cloud APIs are enabled
echo "--> Enabling required GCP APIs..."
$GCLOUD services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    cloudscheduler.googleapis.com \
    secretmanager.googleapis.com \
    --project="${PROJECT_ID}"

# 2. Grant Secret Manager Access to the default Cloud Run service account
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
echo "--> Granting Secret Accessor role to service account: ${COMPUTE_SA}..."
$GCLOUD projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${COMPUTE_SA}" \
    --role="roles/secretmanager.secretAccessor" \
    --condition=None \
    --quiet >/dev/null || true

# 3. Deploy to Cloud Run from source code (built server-side via Cloud Build)
echo "--> Deploying from source code (Google Cloud Build)..."
$GCLOUD run deploy "${SERVICE_NAME}" \
    --source="." \
    --region="${REGION}" \
    --project="${PROJECT_ID}" \
    --platform="managed" \
    --allow-unauthenticated \
    --min-instances=0 \
    --max-instances=1 \
    --memory=512Mi \
    --cpu=1 \
    --quiet \
    --set-secrets="ESPN_S2=ESPN_S2:latest,ESPN_SWID=ESPN_SWID:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest,SENDGRID_API_KEY=SENDGRID_API_KEY:latest" \
    --set-env-vars="NFL_SEASON=2026,GEMINI_MODEL=gemini-2.5-pro"

# Retrieve and display Service URL
SERVICE_URL=$($GCLOUD run services describe "${SERVICE_NAME}" --region="${REGION}" --project="${PROJECT_ID}" --format="value(status.url)")
echo ""
echo "============================================================"
echo "✅ Cloud Run Service deployed successfully!"
echo "   Service URL: ${SERVICE_URL}"
echo "   Health Check: curl ${SERVICE_URL}/health"
echo "============================================================"
