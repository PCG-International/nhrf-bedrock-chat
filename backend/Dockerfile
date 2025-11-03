FROM public.ecr.aws/docker/library/python:3.13.2-slim-bookworm

WORKDIR /backend

COPY ./pyproject.toml ./poetry.lock ./

ENV POETRY_REQUESTS_TIMEOUT=10800
RUN python -m pip install --upgrade pip && \
    pip install poetry --no-cache-dir && \
    poetry config virtualenvs.create false && \
    poetry install --no-interaction --no-ansi --only main && \
    poetry cache clear --all pypi

# Install curl for health checks
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY ./app ./app

ENV PORT=8000
EXPOSE ${PORT}

# Health check for ECS
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]