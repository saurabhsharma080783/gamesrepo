#!/usr/bin/env bash
# The assessment app, local-model settings, idle auto-deallocate timer, sudo rule and login banner.
set -euo pipefail
: "${MODEL_NAME:?}"
SRC="${APPLIANCE_FILES:-/tmp/appliance}"
DIST="${APP_DIST:-/tmp/dist}"

python3 -m venv /opt/azure-assess
/opt/azure-assess/bin/pip install -q --upgrade pip
/opt/azure-assess/bin/pip install -q --no-cache-dir "$DIST"/azure_assessment-*.whl
for cmd in azure-assess assess-run assess-mode assess-idle-check; do
  ln -sf "/opt/azure-assess/bin/$cmd" "/usr/local/bin/$cmd"
done

# Local mode is the default; llm.local.json lets 'assess-mode local' restore it after using a hosted model.
cat > /etc/azure-assess/llm.local.json <<CONF
{
  "hosting": "local",
  "url": "http://127.0.0.1:8080/v1",
  "model": "$MODEL_NAME",
  "timeout": 1800,
  "workers": 1
}
CONF
install -m 0644 /etc/azure-assess/llm.local.json /etc/azure-assess/llm.json
echo "ASSESS_IDLE_MINUTES=60" > /etc/azure-assess/idle.env

install -m 0644 "$SRC/systemd/assess-idle.service" "$SRC/systemd/assess-idle.timer" /etc/systemd/system/
systemctl enable assess-idle.timer
install -m 0440 "$SRC/sudoers-assess" /etc/sudoers.d/azure-assess
visudo -cf /etc/sudoers.d/azure-assess
install -m 0755 "$SRC/motd" /etc/update-motd.d/60-azure-assess
