FROM python:3.12-slim

WORKDIR /app

# Install system deps (chromadb needs sqlite + build tools for hnswlib)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY . .

RUN pip install --no-cache-dir -e .

# Data dir for ChromaDB, cache files, and OAuth tokens
ENV JIMMY_DATA_DIR=/data
RUN mkdir -p /data

EXPOSE 7700

CMD ["python", "-m", "uvicorn", "jimmy.api.server:app", "--host", "0.0.0.0", "--port", "7700", "--workers", "1"]
