#!/usr/bin/env bash
# Clean up before the image is generalised (Packer runs waagent -deprovision+user afterwards).
set -euo pipefail
apt-get -y -q autoremove --purge
apt-get clean
rm -rf /tmp/dist /tmp/appliance /root/.cache /var/lib/azure-assess/*
find /var/log -type f -name '*.log' -exec truncate -s 0 {} +
