# Agent Guidelines & Operational Rules

## 1. Architecture Maintenance via Archify (MANDATORY RULE)

> **User Mandate:**
> *"Going forward, if we are to do any changes that impact the architecture, make sure to add a task (and explicitly ask the user) whether the architecture diagrams (via Archify) need to be updated."*

### Guidelines:
1. **Triggering Conditions**: Any change that touches:
   - System topology, cloud infrastructure, or deployment targets (Cloud Run, Cloud Scheduler)
   - New external APIs, data feeds, or third-party integrations (ESPN, Sleeper, Vegas, Weather, SendGrid)
   - AI models, model negotiation ladders, or LLM routing (Gemini versions, Vertex endpoints)
   - Persistent storage, caching, or memory mechanisms (GCS JSON memory, buckets)
   - Ingress endpoints, cron jobs, or API contracts (FastAPI routes, Pydantic schemas)
2. **Action Required**:
   - Add a concrete task to the plan: *"Review and update Archify architecture diagrams"*.
   - **Ask the user** whether the Archify specifications ([`docs/architecture.json`](docs/architecture.json) and [`docs/api-data-flow.json`](docs/api-data-flow.json)) should be updated to reflect the new design.
   - When approved, update the JSON diagram specs and push to `main`. Pushing to `docs/*.json` automatically triggers GitHub Actions (`.github/workflows/archify.yml`) to compile and render `docs/*.html` and `docs/*.svg` using Archify.

---

## 2. Production Deployment Rule

- **Always ask the user** before initiating deployments or major infrastructure changes to Google Cloud Run.
- Never deploy without explicit user approval.
- Ensure all tests pass (`pytest tests/`) and linter is clean (`ruff check`) before committing or proposing deployment.
