# Assessment appliance image (CPU): Ubuntu 24.04 + llama.cpp model server + model weights + azure-assess.
#
# Build (see ../README.md):
#   python -m pip wheel --no-deps -w dist .        # from azure-assessment/, builds the app wheel
#   packer init deploy/appliance/packer
#   packer build -var-file=deploy/appliance/packer/build.pkrvars.hcl deploy/appliance/packer
#
# The result is a version of an image definition in an Azure Compute Gallery in OUR subscription,
# from which it is shared with, or copied into, customer subscriptions.

packer {
  required_plugins {
    azure = {
      source  = "github.com/hashicorp/azure"
      version = "~> 2.1"
    }
  }
}

variable "subscription_id" {
  type        = string
  description = "Our subscription that holds the image gallery and runs the build VM."
}

variable "gallery_resource_group" {
  type    = string
  default = "rg-assess-images"
}

variable "gallery_name" {
  type    = string
  default = "gal_assess"
}

variable "image_name" {
  type        = string
  default     = "azure-assess-appliance-cpu"
  description = "Image definition in the gallery (created once by setup-gallery.sh)."
}

variable "image_version" {
  type        = string
  description = "Semantic version for this build, e.g. 1.0.0."
}

variable "location" {
  type    = string
  default = "westeurope"
}

variable "replication_regions" {
  type        = list(string)
  default     = []
  description = "Extra regions to replicate the image to (customers deploy from the nearest one)."
}

variable "build_vm_size" {
  type        = string
  default     = "Standard_D8s_v5"
  description = "Only used during the build; the image runs on any size. Needs at least 8 GB RAM for the self-test (e.g. Standard_B4ms for a cheap test build, Standard_D8s_v5 for a fast one)."
}

variable "model_name" {
  type    = string
  default = "qwen2.5:7b-instruct"
}

variable "model_source" {
  type        = string
  default     = "oci:ai/qwen2.5:7B-Q4_K_M"
  description = "oci:<repo>:<tag> (Docker Hub), https://...gguf, or file:/tmp/model/<name>.gguf for offline builds."
}

variable "model_sha256" {
  type        = string
  default     = "7848e617403f9c800ec80c974126803ec949388869dcd3d6ff6e886de0576b9b"
  description = "Pins the exact model file; the build fails on any mismatch."
}

variable "model_license" {
  type    = string
  default = "Apache-2.0"
}

variable "model_file" {
  type        = string
  default     = ""
  description = "Local .gguf to upload for offline builds (then set model_source = file:/tmp/model/<name>)."
}

variable "llama_cpp_python_version" {
  type    = string
  default = "0.3.35"
}

locals {
  app_root = "${path.root}/../../.."
}

source "azure-arm" "appliance" {
  use_azure_cli_auth = true
  subscription_id    = var.subscription_id
  location           = var.location
  vm_size            = var.build_vm_size

  os_type         = "Linux"
  image_publisher = "Canonical"
  image_offer     = "ubuntu-24_04-lts"
  image_sku       = "server"
  os_disk_size_gb = 64

  shared_image_gallery_destination {
    subscription         = var.subscription_id
    resource_group       = var.gallery_resource_group
    gallery_name         = var.gallery_name
    image_name           = var.image_name
    image_version        = var.image_version
    replication_regions  = concat([var.location], var.replication_regions)
    storage_account_type = "Standard_LRS"
  }

  azure_tags = {
    purpose       = "azure-assess appliance image"
    image_version = var.image_version
    model         = var.model_name
  }
}

build {
  sources = ["source.azure-arm.appliance"]

  provisioner "file" {
    source      = "${local.app_root}/dist/"
    destination = "/tmp/dist"
  }

  provisioner "file" {
    source      = "${path.root}/../files/"
    destination = "/tmp/appliance"
  }

  provisioner "shell" {
    inline = ["mkdir -p /tmp/model"]
  }

  dynamic "provisioner" {
    labels   = ["file"]
    for_each = var.model_file == "" ? [] : [var.model_file]
    content {
      source      = provisioner.value
      destination = "/tmp/model/"
    }
  }

  provisioner "shell" {
    execute_command = "chmod +x {{ .Path }}; sudo -E bash -c '{{ .Vars }} {{ .Path }}'"
    environment_vars = [
      "MODEL_NAME=${var.model_name}",
      "MODEL_SOURCE=${var.model_source}",
      "MODEL_SHA256=${var.model_sha256}",
      "MODEL_LICENSE=${var.model_license}",
      "LLAMA_CPP_PYTHON_VERSION=${var.llama_cpp_python_version}",
    ]
    scripts = [
      "${path.root}/../scripts/10-base.sh",
      "${path.root}/../scripts/20-model-server.sh",
      "${path.root}/../scripts/30-model.sh",
      "${path.root}/../scripts/40-app.sh",
      "${path.root}/../scripts/50-selftest.sh",
      "${path.root}/../scripts/90-finalize.sh",
    ]
  }

  # Generalise so every VM created from the image gets its own identity, host keys and admin user.
  provisioner "shell" {
    execute_command = "chmod +x {{ .Path }}; {{ .Vars }} sudo -E sh '{{ .Path }}'"
    inline_shebang  = "/bin/sh -x"
    inline = [
      "rm -rf /tmp/model",
      "/usr/sbin/waagent -force -deprovision+user && export HISTSIZE=0 && sync",
    ]
  }

  post-processor "manifest" {
    output     = "${local.app_root}/dist/appliance-manifest.json"
    strip_path = true
    custom_data = {
      image_version = var.image_version
      model         = var.model_name
      model_sha256  = var.model_sha256
    }
  }
}
