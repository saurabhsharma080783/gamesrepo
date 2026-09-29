# Assessment appliance (CPU VM image)

One VM image containing everything needed to produce assessment reports with AI narrative inside the
customer's Azure environment:

| Layer | Contents |
|---|---|
| OS | Ubuntu 24.04 LTS, patched at build time, automatic security updates, firewall (SSH only) |
| Model server | llama.cpp (`llama-cpp-python` server), OpenAI-compatible API on `127.0.0.1:8080` only; systemd sandbox, no network except loopback |
| Model | Qwen 2.5 7B Instruct, Q4_K_M (Apache 2.0), checksum-pinned |
| App | `azure-assess` plus the appliance commands `assess-run`, `assess-mode` and `assess-idle-check` |

No internet access is needed at the customer's site. The model runs on the VM's CPU (no GPU), starts
only while a report is being drafted, and stops afterwards.

## Two ways to use a model, one image

| Mode | Where the model runs | What leaves the customer's environment | Set with |
|---|---|---|---|
| `local` (default) | On this VM | Nothing | `sudo assess-mode local` |
| `hosted` | On our server | The summarised facts only, over HTTPS, with a per-customer API key | `sudo assess-mode hosted --url https://... --provider "..." --key-file -` |

## Cost

The VM is only needed for about an hour per engagement:

* **CPU only.** Size `Standard_D16s_v5` by default; a report took about 25 minutes on 4 vCPUs in testing,
  and more cores are faster.
* **Deallocates itself.** Use `assess-run ... --deallocate` to stop billing when the report is done. If
  the VM is left running, a timer deallocates it after 60 idle minutes (nobody logged in and no report
  running). A daily auto-shutdown at 19:00 is a further backstop.
* **Only the disk is billed while it is deallocated** (StandardSSD, 64 GB). Delete the VM after the
  engagement and redeploy it from the image next time.
* **Optional Spot pricing** (`useSpot: true`) for a large discount. If Azure evicts the VM, it is
  deallocated, not deleted; start it and rerun.

## Our side: build the image

1. Once: `./setup-gallery.sh <our-subscription-id> westeurope` creates the gallery and image definition.
2. Each release: run the **Build appliance image** GitHub workflow with a version such as `1.0.0`, or run
   it locally:

   ```bash
   cd azure-assessment
   python -m pip wheel --no-deps -w dist .
   az login
   packer init deploy/appliance/packer
   packer build -var subscription_id=<our-sub> -var image_version=1.0.0 deploy/appliance/packer
   ```

The build (about 20 to 30 minutes) creates a temporary VM and runs `scripts/10` to `scripts/90`. The
self-test builds a report and checks that the model answers; if either fails, no image is published.
For an offline build, pass `-var model_file=/path/model.gguf -var model_source=file:/tmp/model/model.gguf`.

To use another model, set `model_name`, `model_source` and `model_sha256`. For example, a 14B model is
more accurate but about twice as slow on CPU.

## Customer side: deploy

With Contributor and User Access Administrator (or Owner) on one resource group in the customer's subscription:

1. **Copy the image** into their subscription:
   `./copy-to-customer.sh <our-image-version-id> <customer-sub> rg-azure-assess <region>`.
   This prints the `imageId`. Later, an Azure Marketplace private offer can replace this step.
2. **Deploy the VM** into an existing subnet reachable from Azure Bastion or the customer's VPN:

   ```bash
   cp bicep/main.parameters.example.json params.json    # fill in imageId, subnetId, sshPublicKey
   az deployment group create -g rg-azure-assess -f bicep/main.bicep -p params.json
   ```

   The VM has no public IP. Its managed identity may deallocate this VM and nothing else.
3. **Check it:** connect through Bastion and run `azure-assess ai-check`. For local mode this needs the
   model running, so `assess-run` is the simpler test.

## Running an assessment

```bash
# copy the customer's export to the VM (e.g. az network bastion tunnel + scp), then:
assess-run inventory.json -c "Fabrikam" --config policy.json
#   -> /srv/assessments/fabrikam/<date-time>/: Word, HTML, findings, and *-ai-draft.json

# a consultant reviews the AI text: edit "paragraphs" in the *-ai-draft.json, then rebuild (no model needed):
assess-run inventory.json -c "Fabrikam" --config policy.json \
  --ai-draft /srv/assessments/fabrikam/<date-time>/fabrikam-azure-assessment-ai-draft.json \
  --reviewed-by "A. Consultant" --deallocate
```

The reports say where the model ran and who reviewed the AI text.

## Hosted mode

```bash
sudo assess-mode hosted --url https://llm.<our-domain>/v1 --provider "<our company>" --key-file - <<< "<api-key>"
azure-assess ai-check
```

The API key is stored in `/etc/azure-assess/llm.key`, readable by root and the `assess` group only; it
is never written into the settings or the reports. Setting up the hosted server is a separate kit.

## Files

| Path | Purpose |
|---|---|
| `packer/appliance.pkr.hcl` | Image build definition |
| `scripts/10-base.sh` | OS updates, packages, accounts, folders, firewall |
| `scripts/20-model-server.sh` | llama.cpp server in `/opt/llm` (portable CPU build) and its systemd unit |
| `scripts/30-model.sh` | Model download (Docker Hub OCI artifact, https or local file) with SHA-256 check |
| `scripts/40-app.sh` | App in `/opt/azure-assess`, local-mode settings, idle timer, sudo rule, login banner |
| `scripts/50-selftest.sh` | Fails the build unless a report builds and the model answers |
| `scripts/90-finalize.sh` | Clean-up before generalisation |
| `files/` | systemd units, sudoers rule, login banner |
| `bicep/main.bicep` | Customer deployment: VM, NIC, NSG, self-deallocate role, auto-shutdown |
| `setup-gallery.sh`, `copy-to-customer.sh` | Gallery setup; copying an image version into a customer subscription |

## What has been tested, and what has not

* **Tested** on Ubuntu 24.04 (the image's OS): scripts 10 to 40 ran for real, including the llama.cpp
  build, model install and checksum rejection, and app install. The appliance commands have automated
  tests against mocked Azure endpoints. ShellCheck, `bicep build` and `packer validate -syntax-only` pass.
* **Not yet tested:** a real Packer build in Azure, deployment of `main.bicep`, and `copy-to-customer.sh`.
  These need an Azure subscription; run them once before the first customer.
