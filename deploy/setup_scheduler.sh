#!/usr/bin/env bash
# =============================================================================
# NFL Fantasy Football Agent — Cloud Scheduler Configuration Script
# =============================================================================
# Creates 3 consolidated cron triggers (100% within GCP Free Tier)
# Timezone: America/New_York (Eastern Time)
# =============================================================================

set -euo pipefail

GCLOUD="/Users/madushan/google-cloud-sdk/bin/gcloud"
PROJECT_ID="gen-lang-client-0581555372"
SERVICE_NAME="fantasy-agent"
REGION="${1:-us-central1}"

echo "============================================================"
echo "  Setting up Cloud Scheduler Cron Jobs"
echo "  Project: ${PROJECT_ID}"
echo "  Region:  ${REGION}"
echo "============================================================"

# Retrieve Cloud Run Service URL
SERVICE_URL=$($GCLOUD run services describe "${SERVICE_NAME}" --region="${REGION}" --project="${PROJECT_ID}" --format="value(status.url)")

if [[ -z "${SERVICE_URL}" ]]; then
    echo "❌ Could not find Cloud Run service ${SERVICE_NAME}. Please deploy the service first."
    exit 1
fi

echo "--> Cloud Run Target URL: ${SERVICE_URL}"

# Helper function to create or update scheduler job
upsert_cron_job() {
    local job_name="$1"
    local cron_schedule="$2"
    local endpoint="$3"
    local description="$4"

    local target_uri="${SERVICE_URL}${endpoint}"

    if $GCLOUD scheduler jobs describe "${job_name}" --location="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
        echo "--> Updating existing job: ${job_name}..."
        $GCLOUD scheduler jobs update http "${job_name}" \
            --location="${REGION}" \
            --schedule="${cron_schedule}" \
            --time-zone="America/New_York" \
            --uri="${target_uri}" \
            --http-method="POST" \
            --description="${description}" \
            --project="${PROJECT_ID}" >/dev/null
    else
        echo "--> Creating job: ${job_name}..."
        $GCLOUD scheduler jobs create http "${job_name}" \
            --location="${REGION}" \
            --schedule="${cron_schedule}" \
            --time-zone="America/New_York" \
            --uri="${target_uri}" \
            --http-method="POST" \
            --description="${description}" \
            --project="${PROJECT_ID}" >/dev/null
    fi

    echo "✅ Scheduled ${job_name} [${cron_schedule} ET] -> ${endpoint}"
}

# 1. Weekly Analysis (Tue: Waivers, Thu: TNF lock, Fri: Injury roundup, Sat: Matchup preview)
upsert_cron_job \
    "weekly-routine" \
    "0 7 * * 2,4,5,6" \
    "/run/weekly" \
    "Weekly fantasy routine: Tuesday waivers, Thursday TNF, Friday injuries, Saturday preview"

# 2. Sunday Early Pregame Inactives (11:30 AM ET - 90 min before 1:00 PM kickoff)
upsert_cron_job \
    "sunday-early-pregame" \
    "30 11 * * 0" \
    "/run/sunday-pregame" \
    "Sunday early inactive check and lineup finalization at 11:30 AM ET"

# 3. Sunday Late Slate Inactives (2:30 PM ET - before 4:05/4:25 PM kickoff)
upsert_cron_job \
    "sunday-late-pregame" \
    "30 14 * * 0" \
    "/run/sunday-pregame" \
    "Sunday late slate inactive check and lineup updates at 2:30 PM ET"

echo ""
echo "============================================================"
echo "✅ All 3 Cloud Scheduler cron triggers configured successfully!"
echo "   All jobs are within GCP's 3 free jobs/month tier."
echo "============================================================"
