# Build remains license-gated: the reviewed Axi wheel and its hash must be supplied by an
# approved build context. The base is the same immutable Python image used by analysis-runtime.
FROM python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2

ARG AXI_WHEEL=deploy/omniagent/execution-runtime/vendor/axi_cli-0.0.11-py3-none-any.whl
ARG CONFIRM_AXI_LICENSE_REVIEWED=

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONNOUSERSITE=1 \
    HOME=/home/user \
    AXI_HOME=/home/user/.axi \
    AXI_RICH=0 \
    PATH=/opt/runtime/bin:$PATH

WORKDIR /workspace

COPY ${AXI_WHEEL} /tmp/axi_cli-0.0.11-py3-none-any.whl
COPY deploy/omniagent/execution-runtime/verify_axi_wheel.py /tmp/verify_axi_wheel.py
COPY packages/agent-eval-axi-tools /src/agent-eval-axi-tools
COPY deploy/omniagent/analysis-runtime/requirements.txt /tmp/runtime-requirements.txt
COPY deploy/omniagent/analysis-runtime/main.py /opt/runtime/main.py

RUN if [ "$CONFIRM_AXI_LICENSE_REVIEWED" != "axi-license-reviewed" ]; then \
        echo "Axi image build requires written license review confirmation" >&2; \
        exit 1; \
    fi \
    && python /tmp/verify_axi_wheel.py /tmp/axi_cli-0.0.11-py3-none-any.whl \
    && python -m venv /opt/runtime \
    && /opt/runtime/bin/pip install --no-cache-dir \
        /tmp/axi_cli-0.0.11-py3-none-any.whl /src/agent-eval-axi-tools \
        -r /tmp/runtime-requirements.txt \
    && rm -rf /tmp/axi_cli-0.0.11-py3-none-any.whl /tmp/verify_axi_wheel.py \
        /src/agent-eval-axi-tools \
        /tmp/runtime-requirements.txt \
    && useradd --uid 10001 --gid users --create-home --home-dir /home/user user \
    && mkdir -p /workspace /home/user/.axi /tmp \
    && chown -R 10001:users /workspace /home/user /tmp

USER 10001:users

EXPOSE 8888
ENTRYPOINT ["/opt/runtime/bin/python", "-m", "uvicorn", "main:app", "--app-dir", "/opt/runtime", "--host", "0.0.0.0", "--port", "8888", "--log-level", "info"]
