# Backend-only image (for CI/testing — no frontend static files)
# For production, use the root Dockerfile which includes everything.
FROM python:3.12-slim AS builder
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
# Install the exact locked dependency versions (reproducible). The runtime
# stage below copies the source and runs `python -m app` from there, so only
# the deps need to be installed here.
RUN uv export --frozen --no-emit-project --format requirements-txt -o /tmp/req.txt \
    && uv pip install --no-cache-dir --system -r /tmp/req.txt

FROM python:3.12-slim
WORKDIR /app
RUN useradd -m -u 1000 labdog
COPY --from=builder /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --chown=labdog:labdog . .
USER labdog
EXPOSE 8000
CMD ["python", "-m", "app", "--no-celery", "--skip-migrate"]
