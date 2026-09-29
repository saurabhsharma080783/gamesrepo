#!/usr/bin/env bash
# Install the assessment appliance (app + local open-source model) on an existing Ubuntu 24.04 VM,
# straight from a clone of this repository. Same result as a VM built from the appliance image.
#
#   git clone <repo> && cd <repo>/azure-assessment/deploy/appliance
#   sudo ./install-appliance.sh
#
# Needs outbound internet (Ubuntu packages, Python packages, the model from Docker Hub) and about
# 20 to 30 minutes. Safe to re-run: finished steps are skipped. Afterwards log out and back in, then:
#   assess-run inventory.json -c "Customer"
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }
# shellcheck source=/dev/null
. /etc/os-release
[ "${VERSION_ID:-}" = "24.04" ] || echo "warning: tested on Ubuntu 24.04; this is ${PRETTY_NAME:-unknown}" >&2
HERE="$(cd "$(dirname "$0")" && pwd)"
APP_ROOT="$(cd "$HERE/../.." && pwd)"
export APPLIANCE_FILES="$HERE/files"
export MODEL_NAME="${MODEL_NAME:-qwen2.5:7b-instruct}"
export MODEL_SOURCE="${MODEL_SOURCE:-oci:ai/qwen2.5:7B-Q4_K_M}"
export MODEL_SHA256="${MODEL_SHA256:-7848e617403f9c800ec80c974126803ec949388869dcd3d6ff6e886de0576b9b}"
export MODEL_LICENSE="${MODEL_LICENSE:-Apache-2.0}"

echo "== 1/5 Operating system packages"
bash "$HERE/scripts/10-base.sh"

echo "== 2/5 Model server (compiles llama.cpp, about 5-10 minutes)"
if ! /opt/llm/bin/python -c "import llama_cpp" 2>/dev/null; then
  bash "$HERE/scripts/20-model-server.sh"
else
  install -m 0644 "$APPLIANCE_FILES/systemd/llm-server.service" /etc/systemd/system/llm-server.service
fi

echo "== 3/5 Model ($MODEL_NAME, about 4.7 GB, checksum-verified)"
if [ ! -f /opt/llm/models/model.gguf ]; then
  bash "$HERE/scripts/30-model.sh"
fi

echo "== 4/5 Assessment app"
APP_DIST="$(mktemp -d)"
export APP_DIST
python3 -m venv "$APP_DIST/venv"
"$APP_DIST/venv/bin/pip" install -q --upgrade pip wheel
"$APP_DIST/venv/bin/pip" wheel -q --no-deps -w "$APP_DIST" "$APP_ROOT"
rm -rf "$APP_ROOT/build"
bash "$HERE/scripts/40-app.sh"
rm -rf "$APP_DIST"
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then
  usermod -aG assess "$SUDO_USER"
fi
systemctl daemon-reload
systemctl start assess-idle.timer

echo "== 5/5 Self-test (builds a report, starts the model and checks it answers)"
if [ "${SKIP_SELFTEST:-0}" != "1" ]; then
  bash "$HERE/scripts/50-selftest.sh"
fi

cat <<DONE

Installed. Log out and back in (to join the 'assess' group), then:

  assess-run <inventory.csv|json> -c "<Customer>" [--config policy.json] [--deallocate]

Reports go to /srv/assessments/<customer>/<date-time>/. Details: $HERE/README.md
The VM deallocates itself after 60 idle minutes only if it has a system-assigned managed identity
with the Virtual Machine Contributor role on itself; otherwise use Auto-shutdown in the Azure portal.
DONE
