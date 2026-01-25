# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder

WORKDIR /app

# Install git (needed for gitpython dependency and cloning repos)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy pyproject.toml first for layer caching
COPY pyproject.toml .

# Install dependencies only (creates a cached layer)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir .

# Copy source code
COPY src/ src/

# Reinstall with source to get the actual package
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir .

# Production stage
FROM python:3.12-slim

WORKDIR /app

# Install git (required at runtime for gitpython)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn

# Create data directory for SQLite persistence
RUN mkdir -p /data && chown appuser:appuser /data

# Switch to non-root user
USER appuser

EXPOSE 8000

ENTRYPOINT ["uvicorn", "jenkins_job_insight.main:app", "--host", "0.0.0.0", "--port", "8000"]
