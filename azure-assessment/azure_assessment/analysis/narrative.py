"""Plain-English description of the customer's Azure environment.

Turns the inventory into an "Infrastructure overview": what the estate consists of,
how it is organised, and how each area is configured, written as readable paragraphs
with supporting tables. Each subsection lists the related findings, so a reader can
correlate the description with the findings and roadmap sections.

Everything is derived from the inventory. When the export lacks configuration data
(for example a portal CSV without ``properties``), the text says what could not be
determined instead of guessing.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from ..models import Resource
from .assessor import Assessment, RuleSummary, friendly_type

# ---- small English helpers ---------------------------------------------------


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n:,} {word if n == 1 else (many or word + 's')}"


def join(items: list[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def lf(text: str) -> str:
    """Lower-case the first letter unless the word is an acronym ('Managed disks' -> 'managed disks',
    'SQL databases' stays)."""
    if text.startswith(_PRODUCT_NAMES) or (len(text) > 1 and text[1].isupper()):
        return text
    return text[0].lower() + text[1:]


_PRODUCT_NAMES = ("App Service", "Azure", "Log Analytics", "Key vault", "SQL", "AKS", "VM ")


def verb(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def pct(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%" if whole else "0%"


def counted(counter: Counter, top: int | None = None, unit: str = "") -> str:
    """'eastus (60), westeurope (39) and uksouth (26)'"""
    return join([f"{k} ({v}{unit})" for k, v in counter.most_common(top)])


def _get(d, *path, default=None):
    for p in path:
        if not isinstance(d, dict):
            return default
        d = d.get(p)
    return default if d is None else d


# ---- categories ---------------------------------------------------------------

CATEGORIES = [
    ("compute", "Compute", ("microsoft.compute/", "microsoft.containerservice/", "microsoft.batch/",
                            "microsoft.desktopvirtualization/")),
    ("network", "Networking", ("microsoft.network/", "microsoft.cdn/")),
    ("storage", "Storage", ("microsoft.storage/", "microsoft.netapp/")),
    ("data", "Databases", ("microsoft.sql/", "microsoft.dbfor", "microsoft.documentdb/", "microsoft.cache/",
                           "microsoft.synapse/")),
    ("web", "Application hosting", ("microsoft.web/", "microsoft.containerinstance/", "microsoft.app/",
                                    "microsoft.containerregistry/")),
    ("security", "Security services", ("microsoft.keyvault/", "microsoft.managedidentity/")),
    ("monitoring", "Monitoring and management", ("microsoft.operationalinsights/", "microsoft.insights/",
                                                 "microsoft.alertsmanagement/", "microsoft.automation/",
                                                 "microsoft.recoveryservices/", "microsoft.operationsmanagement/")),
    ("classic", "Classic (legacy) resources", ("microsoft.classic",)),
]


def category_of(rtype: str) -> str:
    for key, _, prefixes in CATEGORIES:
        if rtype.startswith(prefixes):
            return key
    return "other"


CATEGORY_LABEL = {k: label for k, label, _ in CATEGORIES} | {"other": "Other services"}


# ---- output model -------------------------------------------------------------

@dataclass
class Table:
    headers: list[str]
    rows: list[tuple]
    widths: list[float] | None = None


@dataclass
class NarrativeSection:
    key: str
    title: str
    paragraphs: list[str] = field(default_factory=list)
    bullets: list[str] = field(default_factory=list)
    tables: list[tuple[str, Table]] = field(default_factory=list)   # (caption, table)
    related: list[RuleSummary] = field(default_factory=list)


class _Ctx:
    """Shared lookups for the section builders."""

    def __init__(self, a: Assessment):
        self.a = a
        self.res = a.inventory.resources
        self.n = len(self.res)
        self.has_props = "properties" in a.inventory.available_fields and any(r.properties for r in self.res)
        self.has_tags = "tags" in a.inventory.available_fields
        self.rules = {s.rule.id: s for s in a.rule_summaries}

    def of_type(self, *types: str) -> list[Resource]:
        return [r for r in self.res if r.type in types]

    def in_category(self, cat: str) -> list[Resource]:
        return [r for r in self.res if category_of(r.type) == cat]

    def related(self, *rule_ids: str) -> list[RuleSummary]:
        return [self.rules[i] for i in rule_ids if i in self.rules]

    def no_config(self, what: str) -> str:
        return (f"The inventory export does not include configuration details, so {what} "
                "could not be determined.")


# ---- section builders ---------------------------------------------------------

def _estate(c: _Ctx) -> NarrativeSection:
    a = c.a
    s = NarrativeSection("estate", "Estate at a glance")
    regions = Counter(r.location or "unspecified" for r in c.res)
    subs = Counter(r.subscription_name for r in c.res)
    cats = Counter(category_of(r.type) for r in c.res)
    main_region, main_n = regions.most_common(1)[0]
    s.paragraphs.append(
        f"The environment described by {a.inventory.source or 'the inventory export'} contains "
        f"{plural(c.n, 'Azure resource')} organised into {plural(len(subs), 'subscription')} and "
        f"{plural(a.resource_group_count, 'resource group')}, deployed across {plural(len(regions), 'region')}. "
        f"{main_region} is the primary region, hosting {pct(main_n, c.n)} of all resources"
        + (", followed by " + join([f"{k} ({pct(v, c.n)})" for k, v in regions.most_common()[1:4]]) + "."
           if len(regions) > 1 else "."))
    mix = [f"{CATEGORY_LABEL[k].lower()} ({pct(v, c.n)})" for k, v in cats.most_common()]
    s.paragraphs.append(
        f"By function, the estate is made up of {join(mix)}. The most common resource types are "
        f"{join([f'{lf(t)} ({n})' for t, n in a.by_type(5)])}.")
    vms = c.of_type("microsoft.compute/virtualmachines")
    paas = c.in_category("web") + c.in_category("data")
    if vms or paas:
        style = ("predominantly infrastructure-as-a-service (IaaS), built around virtual machines"
                 if len(vms) >= len(paas) else "predominantly platform-as-a-service (PaaS)")
        s.paragraphs.append(
            f"The hosting model is {style}: there are {plural(len(vms), 'virtual machine')} alongside "
            f"{plural(len(paas), 'managed application or database service')}.")
    s.tables.append(("Resources by functional area", Table(
        ["Area", "Resources", "% of estate", "Main resource types"],
        [(CATEGORY_LABEL[k], v, pct(v, c.n),
          ", ".join(f"{friendly_type(t)} ({n})" for t, n in
                    Counter(r.type for r in c.in_category(k)).most_common(3)))
         for k, v in cats.most_common()], [3, 1.9, 1.8, 6.5])))
    return s


def _subscriptions(c: _Ctx) -> NarrativeSection:
    s = NarrativeSection("subscriptions", "Subscriptions and resource organisation")
    by_sub: dict[str, list[Resource]] = {}
    for r in c.res:
        by_sub.setdefault(r.subscription_name, []).append(r)
    s.paragraphs.append(
        f"Resources are spread across {plural(len(by_sub), 'subscription')}. The breakdown below describes "
        "what each subscription contains, which helps show how workloads and environments are separated.")
    rows = []
    for name, items in sorted(by_sub.items(), key=lambda kv: -len(kv[1])):
        rgs = {r.resource_group.lower() for r in items}
        regions = Counter(r.location or "unspecified" for r in items)
        types = Counter(friendly_type(r.type) for r in items)
        env = ""
        if c.has_tags:
            envs = Counter(v for r in items for k, v in r.tags.items() if k.lower() in ("environment", "env"))
            if envs:
                env = f" Resources are tagged as environment '{envs.most_common(1)[0][0]}'" + (
                    "." if len(envs) == 1 else f", with {len(envs) - 1} other value(s) also in use.")
        s.bullets.append(
            f"{name}: {plural(len(items), 'resource')} in {plural(len(rgs), 'resource group')} across "
            f"{plural(len(regions), 'region')} ({join(sorted(regions))}). It mainly contains "
            f"{join([f'{lf(t)} ({n})' for t, n in types.most_common(3)])}.{env}")
        rows.append((name, len(items), len(rgs), ", ".join(sorted(regions)),
                     ", ".join(f"{t} ({n})" for t, n in types.most_common(3))))
    s.tables.append(("Subscription summary", Table(
        ["Subscription", "Resources", "Resource groups", "Regions", "Main resource types"], rows,
        [2.7, 2.2, 2.2, 2.9, 4.6])))
    rg_sizes = Counter((r.subscription_name, r.resource_group) for r in c.res)
    big = rg_sizes.most_common(3)
    s.paragraphs.append(
        f"On average a resource group holds {c.n / max(len(rg_sizes), 1):.1f} resources. The largest are "
        + join([f"{rg} in {sub} ({n})" for (sub, rg), n in big]) + ".")
    return s


def _regions(c: _Ctx) -> NarrativeSection:
    s = NarrativeSection("regions", "Regional footprint")
    regions = Counter(r.location or "unspecified" for r in c.res)
    s.paragraphs.append(
        f"Resources are deployed in {plural(len(regions), 'region')}: {counted(regions)}. "
        + ("Running in several regions gives the option of regional resilience, but only if workloads are "
           "actually designed to fail over between them." if len(regions) > 1 else
           "Everything runs in a single region, so a regional outage would affect the whole estate."))
    allowed = [x.lower() for x in c.a.config.get("allowed_locations") or []]
    if allowed:
        outside = Counter(r.location for r in c.res if r.location and r.location != "global"
                          and r.location not in allowed)
        s.paragraphs.append(
            f"The approved regions are {join(allowed)}. "
            + (f"{plural(sum(outside.values()), 'resource')} sit outside them, in {counted(outside)}."
               if outside else "All regional resources are within them."))
    rows = []
    for region, n in regions.most_common():
        items = [r for r in c.res if (r.location or "unspecified") == region]
        cats = Counter(CATEGORY_LABEL[category_of(r.type)] for r in items)
        rows.append((region, n, pct(n, c.n), ", ".join(f"{k} ({v})" for k, v in cats.most_common(3))))
    s.tables.append(("Resources by region", Table(["Region", "Resources", "% of estate", "Main areas"], rows,
                                                  [2.6, 1.9, 1.8, 6.9])))
    s.related = c.related("GOV-002")
    return s


def _vm_series(size: str) -> str:
    m = re.match(r"(?:standard|basic)_([a-z]+)\d*[a-z]*(?:_(v\d))?", size.lower())
    if not m:
        return size or "unknown"
    fam = m.group(1).upper()
    return f"{fam}-series" + (f" {m.group(2)}" if m.group(2) else "")


def _compute(c: _Ctx) -> NarrativeSection | None:
    items = c.in_category("compute")
    if not items:
        return None
    s = NarrativeSection("compute", "Compute")
    vms = c.of_type("microsoft.compute/virtualmachines")
    disks = c.of_type("microsoft.compute/disks")
    others = Counter(friendly_type(r.type) for r in items
                     if r.type not in ("microsoft.compute/virtualmachines", "microsoft.compute/disks"))
    if vms:
        s.paragraphs.append(
            f"There are {plural(len(vms), 'virtual machine')} across "
            f"{plural(len({r.subscription_name for r in vms}), 'subscription')} and "
            f"{plural(len({r.location for r in vms}), 'region')}.")
        if c.has_props and any(r.properties for r in vms):
            os_ = Counter(_get(r.properties, "storageProfile", "osDisk", "osType", default="unknown") for r in vms)
            sizes = Counter(_get(r.properties, "hardwareProfile", "vmSize", default="unknown") for r in vms)
            series = Counter(_vm_series(sz) for sz in
                             (_get(r.properties, "hardwareProfile", "vmSize", default="") for r in vms))
            power = Counter(_get(r.properties, "extended", "instanceView", "powerState", "code",
                                 default="PowerState/unknown").split("/")[-1] for r in vms)
            zoned = sum(1 for r in vms if r.zones)
            avset = sum(1 for r in vms if not r.zones and _get(r.properties, "availabilitySet"))
            s.paragraphs.append(
                f"By operating system, the fleet is {counted(os_)}. It uses "
                f"{plural(len(sizes), 'VM size')}, mostly {counted(series, 3)}; the most common sizes are "
                f"{counted(sizes, 3)}.")
            running = power.get("running", 0)
            s.paragraphs.append(
                f"At the time of export {running} of {len(vms)} VMs ({pct(running, len(vms))}) were running"
                + (f", {power['deallocated']} {verb(power['deallocated'], 'was', 'were')} deallocated"
                   if power.get("deallocated") else "")
                + (f" and {power['stopped']} {verb(power['stopped'], 'was', 'were')} stopped but still "
                   "allocated, which means their compute is still being billed" if power.get("stopped") else "")
                + ".")
            s.paragraphs.append(
                f"For resilience, {zoned} {verb(zoned, 'VM is', 'VMs are')} pinned to availability zones and "
                f"{avset} {verb(avset, 'sits', 'sit')} in availability sets; the remaining {len(vms) - zoned - avset} have no zone or availability-set protection and "
                "depend on a single host or datacenter.")
            s.tables.append(("Virtual machines by size", Table(
                ["VM size", "Series", "Count"],
                [(sz, _vm_series(sz), n) for sz, n in sizes.most_common()], [4, 4, 1.5])))
        else:
            s.paragraphs.append(c.no_config("VM sizes, operating systems, power states and zone placement"))
    if disks:
        if c.has_props and any(r.properties for r in disks):
            state = Counter(_get(r.properties, "diskState", default="Unknown") for r in disks)
            gb = sum(int(_get(r.properties, "diskSizeGB", default=0) or 0) for r in disks)
            tiers = Counter(str(r.sku.get("name", "unknown")) for r in disks)
            un = [r for r in disks if _get(r.properties, "diskState") == "Unattached"]
            un_gb = sum(int(_get(r.properties, "diskSizeGB", default=0) or 0) for r in un)
            s.paragraphs.append(
                f"Storage for these machines is provided by {plural(len(disks), 'managed disk')} totalling "
                f"{gb:,} GB, on {counted(tiers)} tiers. Disk states are {counted(state)}."
                + (f" The {plural(len(un), 'unattached disk')} ({un_gb:,} GB) are not used by any VM but are "
                   "still billed." if un else ""))
        else:
            s.paragraphs.append(f"There are {plural(len(disks), 'managed disk')}. "
                                + c.no_config("disk sizes, tiers and attachment state"))
    if others:
        s.paragraphs.append(f"Other compute resources: {counted(others)}.")
    s.related = c.related("REL-001", "REL-002", "COST-003", "COST-004", "COST-001", "OPS-001")
    return s


def _network(c: _Ctx) -> NarrativeSection | None:
    items = c.in_category("network")
    if not items:
        return None
    s = NarrativeSection("network", "Networking")
    types = Counter(friendly_type(r.type) for r in items)
    s.paragraphs.append(f"The network layer consists of {plural(len(items), 'resource')}: {counted(types)}.")
    vnets = c.of_type("microsoft.network/virtualnetworks")
    fws = c.of_type("microsoft.network/azurefirewalls")
    if vnets:
        s.paragraphs.append(
            f"There are {plural(len(vnets), 'virtual network')} in {join(sorted({v.location for v in vnets}))}"
            + (f", with {plural(len(fws), 'Azure Firewall')} deployed" if fws else "")
            + ". How these networks connect to each other (the topology) is assessed in the Architecture "
              "assessment section.")
    nsgs = c.of_type("microsoft.network/networksecuritygroups")
    if nsgs:
        if c.has_props and any(r.properties for r in nsgs):
            rules = sum(len(_get(r.properties, "securityRules", default=[])) for r in nsgs)
            s.paragraphs.append(
                f"Traffic filtering uses {plural(len(nsgs), 'network security group')} with "
                f"{plural(rules, 'custom rule')} between them."
                + (" Some NSGs allow RDP or SSH from the Internet (see related findings)."
                   if "SEC-007" in c.rules else " No NSG was found exposing RDP or SSH to the Internet."))
        else:
            s.paragraphs.append(f"There are {plural(len(nsgs), 'network security group')}. "
                                + c.no_config("the NSG rules"))
    pips = c.of_type("microsoft.network/publicipaddresses")
    if pips:
        if c.has_props:
            unused = [p for p in pips if not (_get(p.properties, "ipConfiguration") or
                                              _get(p.properties, "natGateway"))]
            s.paragraphs.append(
                f"{plural(len(pips), 'public IP address', 'public IP addresses')} are allocated; "
                f"{len(unused)} of them are not associated with any resource.")
        else:
            s.paragraphs.append(f"{plural(len(pips), 'public IP address', 'public IP addresses')} are allocated.")
    s.tables.append(("Network resources", Table(["Resource type", "Count", "Regions"],
                                                [(t, n, ", ".join(sorted({r.location for r in items
                                                                          if friendly_type(r.type) == t})))
                                                 for t, n in types.most_common()], [5, 1.5, 6])))
    s.related = c.related("SEC-007", "COST-002")
    return s


def _storage(c: _Ctx) -> NarrativeSection | None:
    accts = c.of_type("microsoft.storage/storageaccounts")
    if not accts:
        return None
    s = NarrativeSection("storage", "Storage")
    kinds = Counter(r.kind or "unspecified" for r in accts)
    reps = Counter(str(r.sku.get("name", "unknown")).split("_")[-1] for r in accts)
    s.paragraphs.append(
        f"There are {plural(len(accts), 'storage account')}"
        + (f", all of kind {next(iter(kinds))}. " if len(kinds) == 1 else f" ({counted(kinds)}). ")
        + "Replication is " + join([f"{k} for {v}" for k, v in reps.most_common()]) + ". LRS keeps copies within one datacenter only; ZRS spreads them across zones and "
        "GRS/GZRS add a copy in a paired region.")
    if c.has_props and any(r.properties for r in accts):
        https = sum(1 for r in accts if _get(r.properties, "supportsHttpsTrafficOnly", default=True))
        anon = sum(1 for r in accts if _get(r.properties, "allowBlobPublicAccess") is True)
        tls12 = sum(1 for r in accts if _get(r.properties, "minimumTlsVersion") in ("TLS1_2", "TLS1_3"))
        public = sum(1 for r in accts if _get(r.properties, "publicNetworkAccess", default="Enabled") == "Enabled")
        s.paragraphs.append(
            f"On security settings, {https} of {len(accts)} accounts require HTTPS, {tls12} enforce TLS 1.2 or "
            f"later, {anon} allow anonymous blob access, and {public} accept traffic from public networks.")
        s.tables.append(("Storage accounts", Table(
            ["Account", "Subscription", "Replication", "HTTPS only", "Min TLS", "Anonymous blob access"],
            [(r.name, r.subscription_name, r.sku.get("name", ""),
              "Yes" if _get(r.properties, "supportsHttpsTrafficOnly", default=True) else "No",
              _get(r.properties, "minimumTlsVersion", default="-"),
              "Allowed" if _get(r.properties, "allowBlobPublicAccess") is True else "Blocked")
             for r in sorted(accts, key=lambda r: r.name)], [3, 3.4, 2.4, 1.6, 1.6, 2.4])))
    else:
        s.paragraphs.append(c.no_config("encryption-in-transit and public access settings"))
    s.related = c.related("SEC-001", "SEC-002", "SEC-003", "REL-003")
    return s


def _data(c: _Ctx) -> NarrativeSection | None:
    items = c.in_category("data")
    if not items:
        return None
    s = NarrativeSection("data", "Databases")
    servers = c.of_type("microsoft.sql/servers")
    dbs = [r for r in c.of_type("microsoft.sql/servers/databases") if not r.name.endswith("/master")]
    others = Counter(friendly_type(r.type) for r in items
                     if r.type not in ("microsoft.sql/servers", "microsoft.sql/servers/databases"))
    if servers or dbs:
        s.paragraphs.append(
            f"Azure SQL is used with {plural(len(servers), 'logical server')} hosting "
            f"{plural(len(dbs), 'database')}"
            + (f" on the {counted(Counter(str(d.sku.get('name', 'unknown')) for d in dbs))} service tiers."
               if any(d.sku for d in dbs) else "."))
        if c.has_props:
            pub = [r.name for r in servers if _get(r.properties, "publicNetworkAccess") == "Enabled"]
            zr = sum(1 for d in dbs if _get(d.properties, "zoneRedundant"))
            s.paragraphs.append(
                (f"Public network access is enabled on {join(pub)}; " if pub else
                 "No SQL server allows public network access; ")
                + f"{zr} of {len(dbs)} databases {verb(zr, 'is', 'are')} zone redundant.")
    if others:
        s.paragraphs.append(f"Other data services: {counted(others)}.")
    s.related = c.related("SEC-005", "REL-004")
    return s


def _web(c: _Ctx) -> NarrativeSection | None:
    items = c.in_category("web")
    if not items:
        return None
    s = NarrativeSection("web", "Application hosting")
    plans = c.of_type("microsoft.web/serverfarms")
    sites = c.of_type("microsoft.web/sites")
    if plans or sites:
        tiers = Counter(str(p.sku.get("name", "unknown")) for p in plans)
        s.paragraphs.append(
            f"Web workloads run on Azure App Service: {plural(len(sites), 'app')} hosted on "
            f"{plural(len(plans), 'App Service plan')}" + (f" ({counted(tiers)} pricing tiers)." if plans else "."))
        if c.has_props:
            empty = [p.name for p in plans if _get(p.properties, "numberOfSites") == 0]
            https = sum(1 for r in sites if _get(r.properties, "httpsOnly"))
            s.paragraphs.append(
                f"{https} of {len(sites)} apps {verb(https, 'enforces', 'enforce')} HTTPS-only access."
                + (f" The plan(s) {join(empty)} host no apps but still incur charges." if empty else ""))
    others = Counter(friendly_type(r.type) for r in items if r.type not in ("microsoft.web/serverfarms",
                                                                             "microsoft.web/sites"))
    if others:
        s.paragraphs.append(f"Other application services: {counted(others)}.")
    s.related = c.related("SEC-006", "COST-005")
    return s


def _security(c: _Ctx) -> NarrativeSection | None:
    items = c.in_category("security")
    fws = c.of_type("microsoft.network/azurefirewalls")
    if not items and not fws:
        return None
    s = NarrativeSection("security", "Security services")
    kvs = c.of_type("microsoft.keyvault/vaults")
    if kvs:
        s.paragraphs.append(
            f"Secrets, keys and certificates are held in {plural(len(kvs), 'Key Vault')} "
            f"({join(sorted(k.name for k in kvs))}).")
        if c.has_props:
            purge = sum(1 for k in kvs if _get(k.properties, "enablePurgeProtection"))
            s.paragraphs.append(
                f"{purge} of {len(kvs)} vaults {verb(purge, 'has', 'have')} purge protection enabled, which prevents deleted secrets "
                "from being permanently removed during the retention period.")
    else:
        s.paragraphs.append("No Azure Key Vault was found, so application secrets may be stored in code or "
                            "configuration instead of a managed vault.")
    if fws:
        s.paragraphs.append(f"Network perimeter protection is provided by {plural(len(fws), 'Azure Firewall')} "
                            f"in {join(sorted({f.location for f in fws}))}.")
    s.related = c.related("SEC-004")
    return s


def _monitoring(c: _Ctx) -> NarrativeSection:
    s = NarrativeSection("monitoring", "Monitoring and management")
    laws = c.of_type("microsoft.operationalinsights/workspaces")
    items = c.in_category("monitoring")
    if laws:
        s.paragraphs.append(
            f"Logs are collected in {plural(len(laws), 'Log Analytics workspace')} "
            f"({join(sorted(w.name for w in laws))}).")
        if c.has_props:
            ret = Counter(int(_get(w.properties, "retentionInDays", default=30)) for w in laws)
            s.paragraphs.append("Log retention is " + join([f"{d} days on {plural(n, 'workspace')}"
                                                            for d, n in ret.most_common()]) + ".")
    else:
        s.paragraphs.append("No Log Analytics workspace was found in the export, so there is no evidence of "
                            "centralised log collection for these subscriptions.")
    others = Counter(friendly_type(r.type) for r in items if r.type != "microsoft.operationalinsights/workspaces")
    if others:
        s.paragraphs.append(f"Other monitoring and management resources: {counted(others)}.")
    if not c.of_type("microsoft.recoveryservices/vaults"):
        s.paragraphs.append("No Recovery Services vault was found, so there is no evidence of Azure Backup "
                            "protecting the virtual machines or databases.")
    s.related = c.related("OPS-002")
    return s


def _governance(c: _Ctx) -> NarrativeSection:
    s = NarrativeSection("governance", "Governance and tagging")
    req = c.a.config.get("required_tags") or []
    if c.has_tags:
        tagged_any = sum(1 for r in c.res if r.tags)
        s.paragraphs.append(
            f"{tagged_any} of {c.n} resources ({pct(tagged_any, c.n)}) carry at least one tag, and "
            f"{c.a.tag_coverage}% carry all of the required tags ({join(req)}).")
        rows = []
        for t in req:
            have = sum(1 for r in c.res if t.lower() in {k.lower() for k in r.tags})
            values = Counter(v for r in c.res for k, v in r.tags.items() if k.lower() == t.lower())
            rows.append((t, have, pct(have, c.n), ", ".join(f"{k} ({n})" for k, n in values.most_common(4))))
        s.tables.append(("Required tag coverage", Table(["Tag", "Resources tagged", "Coverage", "Most common values"],
                                                        rows, [2.4, 2.4, 1.9, 6.5])))
        keys = Counter(k for r in c.res for k in r.tags)
        s.paragraphs.append(f"In total {plural(len(keys), 'distinct tag key')} are in use, most often "
                            f"{counted(keys, 5)}.")
    else:
        s.paragraphs.append("The inventory export does not include tags, so tagging could not be reviewed.")
    classic = c.in_category("classic")
    if classic:
        s.paragraphs.append(
            f"{plural(len(classic), 'resource')} still {verb(len(classic), 'uses', 'use')} the classic (Azure Service Manager) deployment model "
            f"({join([r.name for r in classic])}). This model is retired, so "
            f"{verb(len(classic), 'it needs', 'they need')} migrating to Azure Resource Manager.")
    s.related = c.related("GOV-001", "GOV-002", "GOV-003")
    return s


def _other(c: _Ctx) -> NarrativeSection | None:
    items = c.in_category("other")
    if not items:
        return None
    s = NarrativeSection("other", "Other services")
    s.paragraphs.append(f"The estate also contains {plural(len(items), 'resource')} of other types: "
                        f"{counted(Counter(friendly_type(r.type) for r in items))}. The current rule set does "
                        "not assess these.")
    return s


BUILDERS = [_estate, _subscriptions, _regions, _compute, _network, _storage, _data, _web, _security,
            _monitoring, _governance, _other]


def build(a: Assessment) -> list[NarrativeSection]:
    if not a.inventory.resources:
        return []
    c = _Ctx(a)
    return [s for s in (b(c) for b in BUILDERS) if s is not None]


def pillar_section_numbers(prefix: str = "5") -> dict[str, str]:
    """Map pillar -> section number in the Word report (e.g. 'Security' -> '5.1')."""
    from ..models import PILLARS
    return {p: f"{prefix}.{i}" for i, p in enumerate(PILLARS, 1)}
