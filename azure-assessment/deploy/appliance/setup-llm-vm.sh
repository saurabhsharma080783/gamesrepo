#!/usr/bin/env bash
# Standalone model VM: the open-source LLM on its own Ubuntu 24.04 VM, called over the private network
# by azure-assess running on another machine (for example a Windows VM in the same virtual network).
#
#   sudo ./setup-llm-vm.sh <client-ip> [<client-ip> ...]
#
# Reuses the image scripts (OS setup, llama.cpp server, checksum-pinned model), then:
#   * listens on port 8080 on all interfaces, but only the given client IPs (and this VM) may connect:
#     enforced by systemd (IPAddressAllow) and the firewall (ufw);
#   * requires an API key, generated here and printed at the end;
#   * starts at boot, since the client cannot start it on demand.
# The single-VM appliance (app and model together) does not need this script.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }
[ $# -ge 1 ] || { echo "usage: sudo $0 <client-ip> [<client-ip> ...]" >&2; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
export APPLIANCE_FILES="$HERE/files"
export MODEL_NAME="${MODEL_NAME:-qwen2.5:7b-instruct}"
export MODEL_SOURCE="${MODEL_SOURCE:-oci:ai/qwen2.5:7B-Q4_K_M}"
export MODEL_SHA256="${MODEL_SHA256:-7848e617403f9c800ec80c974126803ec949388869dcd3d6ff6e886de0576b9b}"
export MODEL_LICENSE="${MODEL_LICENSE:-Apache-2.0}"

bash "$HERE/scripts/10-base.sh"
if ! /opt/llm/bin/python -c "import llama_cpp" 2>/dev/null; then   # skip if already installed (re-runs)
  bash "$HERE/scripts/20-model-server.sh"
fi
if [ ! -f /opt/llm/models/model.gguf ]; then
  bash "$HERE/scripts/30-model.sh"
fi

KEY_FILE=/etc/azure-assess/llm-api.env
if [ ! -f "$KEY_FILE" ]; then
  (umask 077 && printf 'API_KEY=%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" > "$KEY_FILE")
fi
chmod 0600 "$KEY_FILE"

install -d /etc/systemd/system/llm-server.service.d
cat > /etc/systemd/system/llm-server.service.d/network.conf <<CONF
# Standalone model VM: listen on the network for the listed clients only, with an API key.
[Service]
EnvironmentFile=$KEY_FILE
ExecStart=
ExecStart=/bin/sh -c 'exec /opt/llm/bin/python -m llama_cpp.server --model "\$MODEL_PATH" --model_alias "\$MODEL_NAME" --n_ctx 8192 --n_threads "\$(nproc)" --host 0.0.0.0 --port 8080 --api_key "\$API_KEY"'
IPAddressAllow=
IPAddressAllow=localhost $*
CONF

if [ "${SKIP_FIREWALL:-0}" != "1" ]; then
  for ip in "$@"; do ufw allow from "$ip" to any port 8080 proto tcp; done
fi
systemctl daemon-reload
systemctl enable --now llm-server.service

echo "Waiting for the model to load (can take several minutes on first start) ..."
for _ in $(seq 1 450); do
  curl -fsS -H "Authorization: Bearer $(sed -n 's/^API_KEY=//p' "$KEY_FILE")" \
    http://127.0.0.1:8080/v1/models >/dev/null 2>&1 && break
  sleep 2
done
IP=$(hostname -I | awk '{print $1}')
cat <<DONE

Model server ready: http://$IP:8080/v1   model: $MODEL_NAME
Allowed clients: $*
API key (give it to the client machine; stored in $KEY_FILE):
  $(sed -n 's/^API_KEY=//p' "$KEY_FILE")

Also allow TCP 8080 from the client in the network security group, if the subnet or NIC has one.
DONE
