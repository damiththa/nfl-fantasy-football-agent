#!/usr/bin/env bash
# =============================================================================
# NFL Fantasy Football Agent — Secret Manager Setup Script
# =============================================================================
# Reads credentials from your current environment variables (or .env file)
# and uploads them securely to GCP Secret Manager.
# No secrets are hardcoded or committed to git.
# =============================================================================

set -euo pipefail

GCLOUD="/Users/madushan/google-cloud-sdk/bin/gcloud"
PROJECT_ID="gen-lang-client-0581555372"

echo "============================================================"
echo "  Setting up GCP Secret Manager for ${PROJECT_ID}"
echo "============================================================"

# Ensure gcloud is pointing to the correct project
$GCLOUD config set project "${PROJECT_ID}" --quiet

# Enable Secret Manager API if not already enabled
echo "--> Enabling Secret Manager API (secretmanager.googleapis.com)..."
$GCLOUD services enable secretmanager.googleapis.com --project="${PROJECT_ID}"

# Try loading from .env if local env vars are not set
if [[ -f ".env" ]]; then
    echo "--> Sourcing from local .env file..."
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

# Function to create or update a secret
upsert_secret() {
    local secret_name="$1"
    local secret_val="${!secret_name:-}"

    if [[ -z "${secret_val}" ]]; then
        echo "⚠️  Variable ${secret_name} is empty in your environment."
        read -r -s -p "   Please enter ${secret_name}: " secret_val
        echo ""
    fi

    if [[ -z "${secret_val}" ]]; then
        echo "❌ Skipped ${secret_name} (no value provided)."
        return 1
    fi

    # Check if secret already exists
    if $GCLOUD secrets describe "${secret_name}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
        echo "--> Updating existing secret: ${secret_name}..."
        echo -n "${secret_val}" | $GCLOUD secrets versions add "${secret_name}" --data-file=- --project="${PROJECT_ID}" >/dev/null
    else
        echo "--> Creating new secret: ${secret_name}..."
        echo -n "${secret_val}" | $GCLOUD secrets create "${secret_name}" --data-file=- --replication-policy="automatic" --project="${PROJECT_ID}" >/dev/null
    fi

    echo "✅ Secret ${secret_name} securely stored in Secret Manager."
}

# Upload the 3 required secrets
upsert_secret "ESPN_S2"
upsert_secret "ESPN_SWID"
upsert_secret "GEMINI_API_KEY"

# Optional model override
if [[ -n "${GEMINI_MODEL:-}" ]]; then
    upsert_secret "GEMINI_MODEL"
fi

echo "============================================================"
echo "✅ All secrets successfully uploaded to Secret Manager!"
echo "============================================================"
