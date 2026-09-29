#!/usr/bin/env bash
# Model server: llama.cpp (llama-cpp-python, OpenAI-compatible API) in its own virtualenv.
set -euo pipefail
LLAMA_CPP_PYTHON_VERSION="${LLAMA_CPP_PYTHON_VERSION:-0.3.35}"
SRC="${APPLIANCE_FILES:-/tmp/appliance}"

python3 -m venv /opt/llm
/opt/llm/bin/pip install -q --upgrade pip
# GGML_NATIVE=OFF: build for a portable x86-64 baseline (AVX2/FMA) rather than this build VM's exact CPU,
# so the image runs on whatever VM size the customer picks.
CMAKE_ARGS="-DGGML_NATIVE=OFF" /opt/llm/bin/pip install -q --no-cache-dir \
  "llama-cpp-python[server]==${LLAMA_CPP_PYTHON_VERSION}"
install -d -o llm -g llm -m 0755 /opt/llm/models

install -m 0644 "$SRC/systemd/llm-server.service" /etc/systemd/system/llm-server.service
systemctl daemon-reload || true   # no-op when building outside a booted system
# Deliberately not enabled: assess-run starts it on demand and stops it afterwards.
