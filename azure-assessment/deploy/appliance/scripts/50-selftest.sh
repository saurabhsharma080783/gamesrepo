#!/usr/bin/env bash
# Build-time self-test: the app builds a report, and the local model answers. Fails the image build if not.
set -euo pipefail
cd "$(mktemp -d)"
azure-assess demo -c "Self test" -f html -o . >/dev/null
test -s self-test-azure-assessment.html

systemctl start llm-server.service
for _ in $(seq 1 150); do
  curl -fsS http://127.0.0.1:8080/v1/models >/dev/null 2>&1 && break
  sleep 2
done
AZURE_ASSESS_LLM_CONFIG=/etc/azure-assess/llm.json azure-assess ai-check
systemctl stop llm-server.service
rm -rf "$PWD"
