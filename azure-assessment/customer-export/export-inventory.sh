#!/usr/bin/env bash
# Export Azure inventory for the assessment (run by the customer, e.g. in Azure Cloud Shell).
# Needs: Azure CLI with the resource-graph extension, jq, and Reader access.
# Writes inventory-001.json, inventory-002.json, ... (one file per 1,000 resources).
# Optional: pass subscription IDs as arguments to limit scope.
set -euo pipefail

QUERY='Resources | project id, name, type, location, resourceGroup, subscriptionId, kind, sku, tags, zones, properties
| join kind=leftouter (ResourceContainers | where type =~ "microsoft.resources/subscriptions"
    | project subscriptionId, subscriptionName = name) on subscriptionId
| project-away subscriptionId1 | order by id asc'

az extension add --name resource-graph --only-show-errors >/dev/null 2>&1 || true
scope=()
[ "$#" -gt 0 ] && scope=(--subscriptions "$@")

page=1; token=""
while :; do
  out=$(printf "inventory-%03d.json" "$page")
  if [ -n "$token" ]; then
    az graph query -q "$QUERY" --first 1000 --skip-token "$token" "${scope[@]}" -o json > "$out"
  else
    az graph query -q "$QUERY" --first 1000 "${scope[@]}" -o json > "$out"
  fi
  echo "Wrote $out ($(jq '.data | length' "$out") resources)"
  token=$(jq -r '.skip_token // empty' "$out")
  [ -z "$token" ] && break
  page=$((page + 1))
done
echo "Done. Please share all inventory-*.json files."
