FROM python:3.11-slim AS base

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # 默认走清华 PyPI 镜像，避开构建期解析/连到 pypi.org 的间歇性 DNS 失败
    # （"Temporary failure in name resolution"）。可在 build 时用
    # --build-arg PIP_INDEX_URL=https://pypi.org/simple 覆盖回官方源或私服。
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_EXTRA_INDEX_URL=https://pypi.org/simple \
    # 单次连接超时 + 多次重试，吸收瞬时网络/DNS 抖动而非直接失败。
    PIP_DEFAULT_TIMEOUT=60 \
    PIP_RETRIES=5

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --upgrade pip && \
    pip install -e .

COPY alembic.ini ./
COPY alembic ./alembic
RUN find /app/alembic -type d -name __pycache__ -prune -exec rm -rf {} + && \
    find /app/alembic -type f -name '*.pyc' -delete

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "agent_eval.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
