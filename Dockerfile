# ── Jimmy Dockerfile ─────────────────────────────────────────────────────────
# Multi-stage build for smaller final image
#
# Build:  docker build -t jimmy .
# Run:    docker run -p 7700:7700 -v ~/.jimmy:/data --env-file .env.local jimmy

# ── Stage 1: build dependencies ─────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build tools needed by chromadb (hnswlib) and sqlite
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY jimmy/ ./jimmy/

RUN pip install --no-cache-dir --prefix=/install .

# ── Stage 2: runtime ────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Runtime-only system libs (no compiler)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsqlite3-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY jimmy/ ./jimmy/
COPY pyproject.toml ./

# Data dir for ChromaDB, cache, OAuth tokens
ENV JIMMY_DATA_DIR=/data
RUN mkdir -p /data

EXPOSE 7700

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:7700/health || exit 1

CMD ["python", "-m", "uvicorn", "jimmy.api.server:app", \
     "--host", "0.0.0.0", "--port", "7700", "--workers", "1"]
