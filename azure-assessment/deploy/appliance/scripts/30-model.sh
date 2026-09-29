#!/usr/bin/env bash
# Model weights: fetch one GGUF file, verify its SHA-256, and record what was installed.
#
# MODEL_SOURCE is one of:
#   oci:<repo>:<tag>   model packaged as an OCI artifact on Docker Hub, e.g. oci:ai/qwen2.5:7B-Q4_K_M
#   https://...gguf    direct download
#   file:/path.gguf    a file uploaded to the build VM (offline builds)
# MODEL_SHA256 pins the exact file; the build fails if the download does not match.
set -euo pipefail
: "${MODEL_SOURCE:?}" "${MODEL_SHA256:?}" "${MODEL_NAME:?}"
MODEL_LICENSE="${MODEL_LICENSE:-see the model card}"
DEST_DIR="${MODEL_DIR:-/opt/llm/models}"
DEST="$DEST_DIR/model.gguf"
install -d -m 0755 "$DEST_DIR"

oci_json() { curl -fsSL -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.oci.image.manifest.v1+json" "$1"; }

case "$MODEL_SOURCE" in
  oci:*)
    ref="${MODEL_SOURCE#oci:}"; repo="${ref%:*}"; tag="${ref##*:}"
    TOKEN=$(curl -fsSL "https://auth.docker.io/token?service=registry.docker.io&scope=repository:${repo}:pull" |
            python3 -c 'import sys, json; print(json.load(sys.stdin)["token"])')
    manifest=$(oci_json "https://registry-1.docker.io/v2/${repo}/manifests/${tag}")
    digest=$(printf '%s' "$manifest" | python3 -c '
import sys, json
layers = json.load(sys.stdin)["layers"]
print(next(l["digest"] for l in layers if l["mediaType"].startswith("application/vnd.docker.ai.gguf")))')
    if [ "${digest#sha256:}" != "$MODEL_SHA256" ]; then
      echo "error: ${repo}:${tag} now points to ${digest}, not the pinned sha256:${MODEL_SHA256}" >&2
      exit 1
    fi
    curl -fSL --retry 5 -C - -H "Authorization: Bearer $TOKEN" -o "$DEST.part" \
      "https://registry-1.docker.io/v2/${repo}/blobs/${digest}"
    license_digest=$(printf '%s' "$manifest" | python3 -c '
import sys, json
print(next((l["digest"] for l in json.load(sys.stdin)["layers"] if l["mediaType"].endswith(".license")), ""))')
    if [ -n "$license_digest" ]; then
      curl -fsSL -H "Authorization: Bearer $TOKEN" -o "$DEST_DIR/LICENSE" \
        "https://registry-1.docker.io/v2/${repo}/blobs/${license_digest}"
    fi
    ;;
  https://*) curl -fSL --retry 5 -C - -o "$DEST.part" "$MODEL_SOURCE" ;;
  file:*)    cp "${MODEL_SOURCE#file:}" "$DEST.part" ;;
  *) echo "error: unsupported MODEL_SOURCE: $MODEL_SOURCE" >&2; exit 1 ;;
esac

echo "${MODEL_SHA256}  $DEST.part" | sha256sum -c -
mv "$DEST.part" "$DEST"
chown -R llm:llm "$DEST_DIR"
chmod 0444 "$DEST"

cat > /etc/azure-assess/llm-server.env <<CONF
MODEL_PATH=$DEST
MODEL_NAME=$MODEL_NAME
CONF
DEST="$DEST" MODEL_LICENSE="$MODEL_LICENSE" python3 - "$DEST_DIR/MODEL-INFO.json" <<'PY'
import json, os, sys
e = os.environ
info = {"name": e["MODEL_NAME"], "source": e["MODEL_SOURCE"], "sha256": e["MODEL_SHA256"],
        "bytes": os.path.getsize(e["DEST"]), "license": e["MODEL_LICENSE"]}
json.dump(info, open(sys.argv[1], "w"), indent=2)
PY
echo "Installed $MODEL_NAME ($(du -h "$DEST" | cut -f1)) from $MODEL_SOURCE"
