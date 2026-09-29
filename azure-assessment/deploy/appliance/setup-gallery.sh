#!/usr/bin/env bash
# One-off, in OUR subscription: create the resource group, Azure Compute Gallery and image definition
# that Packer builds versions into.
#
#   ./setup-gallery.sh <subscription-id> [location]
set -euo pipefail
SUB="${1:?usage: $0 <subscription-id> [location]}"
LOCATION="${2:-westeurope}"
RG="${GALLERY_RG:-rg-assess-images}"
GALLERY="${GALLERY_NAME:-gal_assess}"
IMAGE="${IMAGE_NAME:-azure-assess-appliance-cpu}"
PUBLISHER="${IMAGE_PUBLISHER:-AzureAssessment}"

az account set --subscription "$SUB"
az group create -n "$RG" -l "$LOCATION" -o none
az sig create -g "$RG" -r "$GALLERY" -l "$LOCATION" -o none
az sig image-definition create -g "$RG" -r "$GALLERY" -i "$IMAGE" -l "$LOCATION" \
  --publisher "$PUBLISHER" --offer azure-assess-appliance --sku cpu \
  --os-type Linux --os-state Generalized --hyper-v-generation V2 \
  --description "Azure assessment appliance: azure-assess + local open-source LLM (CPU)" -o none
echo "Gallery ready: /subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.Compute/galleries/$GALLERY/images/$IMAGE"
