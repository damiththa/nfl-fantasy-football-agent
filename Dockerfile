# =============================================================================
# NFL Fantasy Football Agent — Cloud Run Container
# =============================================================================
FROM python:3.12-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

WORKDIR /app

# Install system dependencies if any
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code and data assets
COPY src/ ./src/
COPY data/ ./data/

# Run as a non-privileged user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Expose port (Cloud Run sets PORT env var)
EXPOSE 8080

# Start FastAPI using Uvicorn
CMD exec uvicorn src.main:app --host 0.0.0.0 --port ${PORT}
