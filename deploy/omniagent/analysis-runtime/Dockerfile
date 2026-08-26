FROM python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONNOUSERSITE=1 \
    HOME=/tmp

RUN groupadd --gid 10001 runtime \
    && useradd --uid 10001 --gid 10001 --no-create-home runtime \
    && python -m venv /opt/runtime \
    && mkdir -p /workspace \
    && chown 10001:10001 /workspace

COPY deploy/omniagent/analysis-runtime/requirements.txt /tmp/requirements.txt
RUN /opt/runtime/bin/pip install --no-cache-dir --requirement /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY deploy/omniagent/analysis-runtime/main.py /opt/runtime/main.py

WORKDIR /workspace
USER 10001:10001
EXPOSE 8888
ENTRYPOINT ["/opt/runtime/bin/python", "-m", "uvicorn", "main:app", "--app-dir", "/opt/runtime", "--host", "0.0.0.0", "--port", "8888", "--log-level", "info"]
