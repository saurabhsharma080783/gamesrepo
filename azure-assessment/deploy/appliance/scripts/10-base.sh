#!/usr/bin/env bash
# Base OS: patches, build tools, automatic security updates, accounts and folders.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update -q
apt-get upgrade -y -q
apt-get install -y -q --no-install-recommends \
  python3 python3-venv python3-dev build-essential cmake curl ca-certificates unattended-upgrades
# Security updates keep installing between image releases.
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'CONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
CONF

# 'assess' group: people who run assessments. 'llm' user: runs the model server, no login.
getent group assess >/dev/null || groupadd --system assess
id llm >/dev/null 2>&1 || useradd --system --home-dir /opt/llm --shell /usr/sbin/nologin llm
install -d -m 0755 /etc/azure-assess
install -d -m 0750 -g assess /var/lib/azure-assess
install -d -m 2770 -g assess /srv/assessments   # setgid: reports stay readable by the group

# Inbound: SSH only (the deployment template also gives the VM no public IP).
if [ "${SKIP_FIREWALL:-0}" != "1" ]; then
  apt-get install -y -q ufw
  ufw allow OpenSSH
  ufw --force enable
fi
