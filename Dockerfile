FROM python:3.11-slim AS base

# Install system deps needed for C extensions, then clean up
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# Set workdir
WORKDIR /app

# Install Python deps first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY backend/ backend/
COPY frontend/ frontend/
COPY version.json .

# Create non-root user
RUN groupadd --gid 1000 localmind \
    && useradd --uid 1000 --gid 1000 --create-home localmind

# Create workspace directory owned by localmind
RUN mkdir -p /data/workspace && chown -R localmind:localmind /data

# Environment variables
# WORKSPACE_DIR is what backend/config.py actually reads via os.getenv
ENV WORKSPACE_DIR=/data/workspace \
    DB_PATH=/data/localmind.db \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run as non-root
USER localmind

CMD ["uvicorn", "backend.server:app", "--host", "0.0.0.0", "--port", "8000"]
