# syntax=docker/dockerfile:1.4
# Multi-stage build for better caching and smaller final image

# Stage 1: Base dependencies (rarely changes)
FROM python:3.12-slim-bookworm AS python-base

# Set environment for better caching
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_VERSION=1.8.3 \
    POETRY_HOME="/opt/poetry" \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1 \
    POETRY_REQUESTS_TIMEOUT=10800

# Add poetry to PATH
ENV PATH="$POETRY_HOME/bin:$PATH"

# Stage 2: Builder with system dependencies
FROM python-base AS builder-base

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        git \
        curl \
        && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN --mount=type=cache,target=/root/.cache \
    curl -sSL https://install.python-poetry.org | python3 -

WORKDIR /build

# Stage 3: Python dependencies (changes when poetry.lock changes)
FROM builder-base AS python-deps

# Copy ONLY dependency files first for better caching
COPY pyproject.toml poetry.lock ./

# Install dependencies with BuildKit cache mount
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=cache,target=/root/.cache/pypoetry \
    poetry install --only main --no-root && \
    pip install rapidocr-onnxruntime==1.4.3

# Stage 4: Runtime base with system packages
FROM python:3.12-slim-bookworm AS runtime-base

# Install runtime dependencies only
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
        ffmpeg \
        && rm -rf /var/lib/apt/lists/*

# Stage 5: Final runtime image
FROM runtime-base AS runtime

WORKDIR /backend

# Set runtime environment variables
ENV HOME=/tmp \
    XDG_CACHE_HOME=/tmp \
    HF_HOME=/tmp/huggingface \
    HUGGINGFACE_HUB_CACHE=/tmp/huggingface/hub \
    TRANSFORMERS_CACHE=/tmp/huggingface/transformers \
    HF_DATASETS_CACHE=/tmp/huggingface/datasets \
    HF_HUB_DISABLE_TELEMETRY=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

# Copy installed packages from python-deps stage
COPY --from=python-deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=python-deps /usr/local/bin /usr/local/bin

# Copy app code (this layer changes most frequently)
COPY ./app ./app

# Expose port for ECS
EXPOSE ${PORT}

# Health check for ECS
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD curl -f http://localhost:${PORT}/health || exit 1

# Run app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]