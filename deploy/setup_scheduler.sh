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

# 1. Tuesday Waiver Wire & Friday Weekend Injury Lock (7:00 PM ET)
# Tuesday: Ingests full Monday Night Football stats & Tuesday practice reports before waivers process overnight
# Friday: Ingests official weekend injury designations (Out, Doubtful, Questionable) from all 32 NFL teams
upsert_cron_job \
    "weekly-routine" \
    "0 19 * * 2,5" \
    "/run/weekly" \
    "Tuesday 7:00 PM ET waiver wire priority & Friday 7:00 PM ET weekend injury lock"

# 2. Thursday Night Football Inactives (6:50 PM ET - 5 mins after official 90-min inactives drop)
upsert_cron_job \
    "thursday-tnf-lock" \
    "50 18 * * 4" \
    "/run/weekly" \
    "Thursday 6:50 PM ET official TNF 90-minute inactives and start/sit confirmation"

# 3. Sunday Game Day Kickoff Inactives (11:45 AM ET & 2:45 PM ET)
# 11:45 AM ET: Captures official inactives and final stadium weather 75 mins before 1:00 PM kickoff
# 2:45 PM ET: Captures late-afternoon slate inactives for final 4:05/4:25 PM flex swaps
upsert_cron_job \
    "sunday-gameday-inactives" \
    "45 11,14 * * 0" \
    "/run/sunday-pregame" \
    "Sunday 11:45 AM ET (1:00 PM kickoff) and 2:45 PM ET (late slate) official inactives"

echo ""
echo "============================================================"
echo "✅ All 3 Cloud Scheduler cron triggers configured successfully!"
echo "   All jobs are within GCP's 3 free jobs/month tier."
echo "============================================================"
