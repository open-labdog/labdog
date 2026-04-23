# Backend-only image (for CI/testing — no frontend static files)
# For production, use the root Dockerfile which includes everything.
FROM python:3.12-slim AS builder
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml .
COPY app/ app/
RUN uv pip install --no-cache-dir --system . || pip install --no-cache-dir .

FROM python:3.12-slim
WORKDIR /app
RUN useradd -m -u 1000 labdog
COPY --from=builder /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --chown=labdog:labdog . .
USER labdog
EXPOSE 8000
CMD ["python", "-m", "app", "--no-celery", "--skip-migrate"]
