"""Assessment rules, grouped by Azure Well-Architected pillar plus Governance.

Each rule declares which resource types it applies to and a ``check`` that returns
``None`` when the resource passes, or a short detail string when it fails.
Rules are plain data, so adding one is a single ``Rule(...)`` entry.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..models import Resource

Check = Callable[[Resource, dict], "str | None"]


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    pillar: str
    severity: str
    types: tuple[str, ...]  # lower-case ARM types; ("*",) means every resource
    check: Check
    recommendation: str
    # Input fields the check reads. If the customer's file lacks one, the rule is reported
    # as "not assessed" rather than counted as a pass.
    needs: tuple[str, ...] = ("properties",)

    def applies_to(self, r: Resource) -> bool:
        # "*" matches everything; a trailing "*" matches a type prefix (e.g. "microsoft.classic*").
        return any(t == "*" or r.type == t or (t.endswith("*") and r.type.startswith(t[:-1])) for t in self.types)


def _get(d, *path, default=None):
    for p in path:
        if not isinstance(d, dict):
            return default
        d = d.get(p)
    return default if d is None else d


# ---- Governance -------------------------------------------------------------

def _missing_tags(r: Resource, cfg: dict):
    required = cfg.get("required_tags") or []
    present = {k.lower() for k in r.tags}
    missing = [t for t in required if t.lower() not in present]
    return f"Missing tag(s): {', '.join(missing)}" if missing else None


def _disallowed_region(r: Resource, cfg: dict):
    allowed = [a.lower() for a in cfg.get("allowed_locations") or []]
    if not allowed or not r.location or r.location == "global":
        return None
    return None if r.location in allowed else f"Deployed in '{r.location}', outside approved regions"


def _classic(r: Resource, cfg: dict):
    return "Classic (ASM) deployment model resource"


# ---- Security ---------------------------------------------------------------

def _st_https(r, cfg):
    return None if _get(r.properties, "supportsHttpsTrafficOnly", default=True) else "HTTP traffic is permitted"


def _st_public_blob(r, cfg):
    return "Anonymous blob access is allowed" if _get(r.properties, "allowBlobPublicAccess") is True else None


def _st_tls(r, cfg):
    tls = _get(r.properties, "minimumTlsVersion", default="TLS1_0")
    return None if tls in ("TLS1_2", "TLS1_3") else f"Minimum TLS version is {tls}"


def _kv_purge(r, cfg):
    return None if _get(r.properties, "enablePurgeProtection") else "Purge protection is disabled"


def _sql_public(r, cfg):
    return "Public network access is enabled" if _get(r.properties, "publicNetworkAccess") == "Enabled" else None


def _web_https(r, cfg):
    return None if _get(r.properties, "httpsOnly") else "HTTPS-only is not enforced"


_MGMT_PORTS = {"22", "3389", "*"}
_ANY_SOURCES = {"*", "0.0.0.0/0", "internet", "any"}


def _port_hits(rule_props) -> set[str]:
    ports = set()
    for p in [rule_props.get("destinationPortRange")] + list(rule_props.get("destinationPortRanges") or []):
        if not p:
            continue
        if p == "*":
            ports.add("*")
        elif "-" in p:
            lo, hi = (int(x) for x in p.split("-", 1))
            ports.update(x for x in ("22", "3389") if lo <= int(x) <= hi)
        elif p in _MGMT_PORTS:
            ports.add(p)
    return ports


def _nsg_open_mgmt(r, cfg):
    exposed = []
    for rule in _get(r.properties, "securityRules", default=[]):
        p = rule.get("properties", rule)
        if str(p.get("direction", "")).lower() != "inbound" or str(p.get("access", "")).lower() != "allow":
            continue
        sources = [p.get("sourceAddressPrefix")] + list(p.get("sourceAddressPrefixes") or [])
        if not any(str(s).lower() in _ANY_SOURCES for s in sources if s):
            continue
        hits = _port_hits(p)
        if hits:
            exposed.append(f"{rule.get('name', '?')} ({'/'.join(sorted(hits))})")
    return f"Internet-exposed management ports: {', '.join(exposed)}" if exposed else None


# ---- Reliability ------------------------------------------------------------

def _vm_no_ha(r, cfg):
    if r.zones or _get(r.properties, "availabilitySet") or _get(r.properties, "virtualMachineScaleSet"):
        return None
    return "Not deployed to an availability zone, availability set or scale set"


def _vm_unmanaged(r, cfg):
    return "Uses unmanaged (VHD) OS disk" if _get(r.properties, "storageProfile", "osDisk", "vhd") else None


def _st_lrs(r, cfg):
    sku = str(r.sku.get("name", ""))
    return f"Replication is {sku} (single datacenter)" if sku.endswith("_LRS") else None


def _sqldb_zr(r, cfg):
    if str(r.sku.get("name", "")).lower() in ("basic", "free") or r.name.endswith("/master"):
        return None
    return None if _get(r.properties, "zoneRedundant") else "Database is not zone redundant"


# ---- Cost Optimization ------------------------------------------------------

def _disk_unattached(r, cfg):
    if _get(r.properties, "diskState") == "Unattached":
        return f"Unattached {r.sku.get('name', '')} disk, {_get(r.properties, 'diskSizeGB', default='?')} GB"
    return None


def _pip_unused(r, cfg):
    if _get(r.properties, "ipConfiguration") or _get(r.properties, "natGateway"):
        return None
    return "Public IP is not associated with any resource"


def _vm_stopped(r, cfg):
    code = _get(r.properties, "extended", "instanceView", "powerState", "code", default="")
    return "VM is stopped but still allocated (compute is billed)" if code == "PowerState/stopped" else None


def _vm_deallocated(r, cfg):
    code = _get(r.properties, "extended", "instanceView", "powerState", "code", default="")
    return "VM is deallocated; disks and IPs still incur cost" if code == "PowerState/deallocated" else None


def _asp_empty(r, cfg):
    return "App Service plan hosts no apps" if _get(r.properties, "numberOfSites") == 0 else None


# ---- Operational Excellence -------------------------------------------------

_LEGACY_SIZE_PREFIXES = ("standard_a", "basic_a", "standard_d1", "standard_ds1", "standard_d2_v2",
                         "standard_ds2_v2", "standard_d3_v2", "standard_ds3_v2")


def _vm_legacy_size(r, cfg):
    size = str(_get(r.properties, "hardwareProfile", "vmSize", default=""))
    return f"Previous-generation size {size}" if size.lower().startswith(_LEGACY_SIZE_PREFIXES) else None


def _law_retention(r, cfg):
    days = _get(r.properties, "retentionInDays", default=30)
    target = cfg.get("min_log_retention_days", 90)
    return f"Retention is {days} days (target ≥ {target})" if days < target else None


VM = "microsoft.compute/virtualmachines"
ST = "microsoft.storage/storageaccounts"

RULES: list[Rule] = [
    Rule("GOV-001", "Required tags missing", "Governance", "Medium", ("*",), _missing_tags,
         "Enforce the tagging standard with Azure Policy (Require/Inherit a tag) and back-fill existing resources.",
         needs=("tags",)),
    Rule("GOV-002", "Resource outside approved regions", "Governance", "Medium", ("*",), _disallowed_region,
         "Apply the 'Allowed locations' policy and plan migration of out-of-policy resources.", needs=()),
    Rule("GOV-003", "Classic (ASM) resource in use", "Governance", "High", ("microsoft.classic*",), _classic,
         "Migrate classic resources to Azure Resource Manager before platform retirement.", needs=()),

    Rule("SEC-001", "Storage account permits HTTP", "Security", "High", (ST,), _st_https,
         "Enable 'Secure transfer required' on the storage account."),
    Rule("SEC-002", "Storage account allows anonymous blob access", "Security", "High", (ST,), _st_public_blob,
         "Set allowBlobPublicAccess to false and use SAS or Entra ID for access."),
    Rule("SEC-003", "Storage account accepts TLS < 1.2", "Security", "Medium", (ST,), _st_tls,
         "Set minimum TLS version to 1.2."),
    Rule("SEC-004", "Key Vault purge protection disabled", "Security", "Medium",
         ("microsoft.keyvault/vaults",), _kv_purge,
         "Enable purge protection to guard secrets and keys against malicious or accidental deletion."),
    Rule("SEC-005", "SQL server reachable from public networks", "Security", "Medium",
         ("microsoft.sql/servers",), _sql_public,
         "Disable public network access and connect through Private Endpoints."),
    Rule("SEC-006", "Web app does not enforce HTTPS", "Security", "High", ("microsoft.web/sites",), _web_https,
         "Enable 'HTTPS Only' on the App Service."),
    Rule("SEC-007", "NSG exposes management ports to the Internet", "Security", "High",
         ("microsoft.network/networksecuritygroups",), _nsg_open_mgmt,
         "Remove Internet-sourced RDP/SSH rules; use Azure Bastion or Just-in-Time VM access."),

    Rule("REL-001", "VM has no zone or availability set", "Reliability", "Medium", (VM,), _vm_no_ha,
         "Deploy production VMs across availability zones or in a scale set / availability set."),
    Rule("REL-002", "VM uses unmanaged disks", "Reliability", "High", (VM,), _vm_unmanaged,
         "Convert to managed disks (unmanaged disks are retired)."),
    Rule("REL-003", "Storage account uses locally redundant replication", "Reliability", "Low", (ST,), _st_lrs,
         "Use ZRS/GZRS for production data that needs zone or regional resilience.", needs=("sku",)),
    Rule("REL-004", "SQL database not zone redundant", "Reliability", "Low",
         ("microsoft.sql/servers/databases",), _sqldb_zr,
         "Enable zone redundancy on business-critical databases."),

    Rule("COST-001", "Unattached managed disk", "Cost Optimization", "Medium",
         ("microsoft.compute/disks",), _disk_unattached,
         "Snapshot if required, then delete orphaned disks."),
    Rule("COST-002", "Unassociated public IP address", "Cost Optimization", "Low",
         ("microsoft.network/publicipaddresses",), _pip_unused,
         "Release public IPs that are not attached to any resource."),
    Rule("COST-003", "VM stopped but not deallocated", "Cost Optimization", "High", (VM,), _vm_stopped,
         "Deallocate stopped VMs (Stop from the portal/CLI) or schedule auto-shutdown."),
    Rule("COST-004", "Deallocated VM", "Cost Optimization", "Low", (VM,), _vm_deallocated,
         "Confirm the VM is still needed; delete it and its disks if not."),
    Rule("COST-005", "Empty App Service plan", "Cost Optimization", "Medium",
         ("microsoft.web/serverfarms",), _asp_empty,
         "Delete or consolidate App Service plans with no apps."),

    Rule("OPS-001", "Previous-generation VM size", "Operational Excellence", "Low", (VM,), _vm_legacy_size,
         "Resize to current generation (v5/v6) SKUs for better price-performance."),
    Rule("OPS-002", "Log Analytics retention below target", "Operational Excellence", "Low",
         ("microsoft.operationalinsights/workspaces",), _law_retention,
         "Increase retention or archive to meet audit and investigation requirements."),
]
