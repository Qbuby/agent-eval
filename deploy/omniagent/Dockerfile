FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/OmniAgent/.omniagent/overlay:/OmniAgent \
    PATH=/OmniAgent/.venv/bin:$PATH

WORKDIR /OmniAgent

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
COPY omniagent ./omniagent

RUN pip install --no-cache-dir uv \
    && uv sync --frozen --no-dev

EXPOSE 8090

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=12 \
    CMD curl -fsS http://localhost:8090/openapi.json >/dev/null || exit 1

ENTRYPOINT ["omniagent"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8090"]
