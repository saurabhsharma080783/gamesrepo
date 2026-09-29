#!/usr/bin/env bash
# Copy an appliance image version from OUR gallery into a CUSTOMER subscription (different tenant),
# as a managed image the deployment template can use. Needs azcopy and the Azure CLI.
#
#   ./copy-to-customer.sh <our-image-version-id> <customer-subscription-id> <customer-rg> <region>
#
# Steps: (1) in our tenant, create a temporary disk from the image version and get a read-only SAS URL;
# (2) sign in to the customer's tenant, create an empty upload disk there and copy the data with azcopy;
# (3) create a managed image from that disk. The customer's copy has no link back to our subscription.
set -euo pipefail
VERSION_ID="${1:?usage: $0 <our-image-version-id> <customer-subscription-id> <customer-rg> <region>}"
CUST_SUB="${2:?}"
CUST_RG="${3:?}"
REGION="${4:?}"
NAME="azure-assess-appliance-$(basename "$VERSION_ID" | tr . -)"
OUR_SUB=$(cut -d/ -f3 <<<"$VERSION_ID")
OUR_RG="${EXPORT_RG:-rg-assess-images}"
TMP_DISK="export-${NAME}"

echo "1/3 Exporting $VERSION_ID from our subscription ..."
az account set --subscription "$OUR_SUB"
az disk create -g "$OUR_RG" -n "$TMP_DISK" -l "$REGION" --gallery-image-reference "$VERSION_ID" -o none
trap 'az account set --subscription "$OUR_SUB"; az disk revoke-access -g "$OUR_RG" -n "$TMP_DISK" -o none || true;
      az disk delete -g "$OUR_RG" -n "$TMP_DISK" --yes -o none || true' EXIT
SRC_SAS=$(az disk grant-access -g "$OUR_RG" -n "$TMP_DISK" --duration-in-seconds 14400 --access-level Read \
          --query accessSAS -o tsv)
BYTES=$(az disk show -g "$OUR_RG" -n "$TMP_DISK" --query diskSizeBytes -o tsv)

echo "2/3 Sign in to the customer's tenant (a browser window or device code will appear) ..."
az login --output none
az account set --subscription "$CUST_SUB"
az group create -n "$CUST_RG" -l "$REGION" -o none
az disk create -g "$CUST_RG" -n "$NAME" -l "$REGION" --for-upload --upload-size-bytes "$((BYTES + 512))" \
  --sku StandardSSD_LRS --os-type Linux --hyper-v-generation V2 -o none
DST_SAS=$(az disk grant-access -g "$CUST_RG" -n "$NAME" --duration-in-seconds 14400 --access-level Write \
          --query accessSAS -o tsv)
azcopy copy "$SRC_SAS" "$DST_SAS" --blob-type PageBlob
az disk revoke-access -g "$CUST_RG" -n "$NAME" -o none

echo "3/3 Creating the managed image in the customer's subscription ..."
DISK_ID=$(az disk show -g "$CUST_RG" -n "$NAME" --query id -o tsv)
IMAGE_ID=$(az image create -g "$CUST_RG" -n "$NAME" -l "$REGION" --source "$DISK_ID" --os-type Linux \
           --hyper-v-generation V2 --query id -o tsv)
az disk delete -g "$CUST_RG" -n "$NAME" --yes -o none
echo "Done. Deploy with imageId = $IMAGE_ID"
