"""Architecture assessment: identify the patterns the environment follows.

For each architecture dimension (network topology, hybrid connectivity, traffic flow and
perimeter security, subscription model, application hosting, resilience and DR, operations)
this module states the pattern it identified, how confident it is, the evidence from the
inventory, and considerations against Microsoft's reference architectures (Cloud Adoption
Framework landing zones and the Well-Architected Framework).

Only what a resource inventory can show is assessed. Identity (Entra ID), RBAC, Azure Policy
and management groups are not in a resource export, so they are called out as out of scope.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ..models import Resource
from .assessor import Assessment, RuleSummary, friendly_type

VNET = "microsoft.network/virtualnetworks"
FIREWALL = "microsoft.network/azurefirewalls"
VNET_GW = "microsoft.network/virtualnetworkgateways"
BASTION = "microsoft.network/bastionhosts"
APPGW = "microsoft.network/applicationgateways"
FRONTDOOR = ("microsoft.network/frontdoors", "microsoft.cdn/profiles")
PE = "microsoft.network/privateendpoints"
ROUTE_TABLE = "microsoft.network/routetables"

PLATFORM_SUBNETS = {"azurefirewallsubnet": "Azure Firewall", "gatewaysubnet": "VPN/ExpressRoute gateway",
                    "azurebastionsubnet": "Azure Bastion", "routeserversubnet": "Route Server",
                    "azurefirewallmanagementsubnet": "Azure Firewall"}

# Azure region pairs (subset covering commonly used regions)
REGION_PAIRS = {
    "eastus": "westus", "eastus2": "centralus", "centralus": "eastus2", "westus": "eastus",
    "westus2": "westcentralus", "westus3": "eastus", "northcentralus": "southcentralus",
    "southcentralus": "northcentralus", "northeurope": "westeurope", "westeurope": "northeurope",
    "uksouth": "ukwest", "ukwest": "uksouth", "francecentral": "francesouth", "germanywestcentral": "germanynorth",
    "swedencentral": "swedensouth", "switzerlandnorth": "switzerlandwest", "norwayeast": "norwaywest",
    "centralindia": "southindia", "southindia": "centralindia", "westindia": "southindia",
    "eastasia": "southeastasia", "southeastasia": "eastasia", "japaneast": "japanwest", "japanwest": "japaneast",
    "australiaeast": "australiasoutheast", "australiasoutheast": "australiaeast", "canadacentral": "canadaeast",
    "canadaeast": "canadacentral", "brazilsouth": "southcentralus", "koreacentral": "koreasouth",
    "uaenorth": "uaecentral", "southafricanorth": "southafricawest",
}

CONFIDENCE_NOTE = {
    "High": "the inventory contains direct configuration evidence",
    "Medium": "inferred from resource types and naming, without full configuration evidence",
    "Low": "the export lacks the data needed; treat as indicative only",
}


def _get(d, *path, default=None):
    for p in path:
        if not isinstance(d, dict):
            return default
        d = d.get(p)
    return default if d is None else d


def _join(items) -> str:
    items = [str(i) for i in items if i]
    if not items:
        return ""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _n(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def _vnet_of(subnet_id: str) -> str:
    """'/subscriptions/../virtualNetworks/v1/subnets/s1' -> lower-cased VNet id."""
    s = (subnet_id or "").lower()
    return s.split("/subnets/")[0] if "/subnets/" in s else ""


# ---- output model -------------------------------------------------------------

@dataclass
class ArchDimension:
    key: str
    title: str
    pattern: str
    confidence: str                      # High / Medium / Low
    summary: list[str] = field(default_factory=list)          # paragraphs
    evidence: list[str] = field(default_factory=list)         # bullets
    considerations: list[str] = field(default_factory=list)   # bullets, vs reference architecture
    tables: list[tuple[str, list[str], list[tuple], list[float]]] = field(default_factory=list)
    related: list[RuleSummary] = field(default_factory=list)


@dataclass
class VNetNode:
    id: str
    name: str
    subscription: str
    location: str
    address_space: list[str]
    subnets: list[str]
    platform_services: list[str]          # "Azure Firewall", "VPN gateway", ...
    role: str = "spoke"                   # hub / spoke / isolated / peer
    hub: str | None = None                # for spokes: the hub they attach to
    default_route_to_nva: bool = False


@dataclass
class Topology:
    pattern: str
    confidence: str
    nodes: dict[str, VNetNode]
    edges: list[tuple[str, str, str]]     # (a, b, kind) kind: hub-spoke / hub-hub / spoke-spoke / other
    hubs: list[str]
    spokes: list[str]
    isolated: list[str]
    external_peers: int
    onprem: list[dict]                    # {"hub": vnet id, "type": "VPN" | "ExpressRoute", "name": ...}
    has_peering_data: bool
    vwan: bool


@dataclass
class Architecture:
    topology: Topology
    dimensions: list[ArchDimension]

    @property
    def headline(self) -> str:
        d = {x.key: x for x in self.dimensions}
        labels = {"network": "network topology", "hybrid": "hybrid connectivity", "landing_zone": "subscription model"}
        return ("Architecture at a glance: " + "; ".join(f"{labels[k]} is {d[k].pattern[0].lower()}{d[k].pattern[1:]}"
                                                         for k in labels if k in d) + ".")


# ---- analysis -----------------------------------------------------------------

class _Ctx:
    def __init__(self, a: Assessment):
        self.a = a
        self.res = a.inventory.resources
        self.has_props = "properties" in a.inventory.available_fields and any(r.properties for r in self.res)
        self.rules = {s.rule.id: s for s in a.rule_summaries}
        self.by_type: dict[str, list[Resource]] = defaultdict(list)
        for r in self.res:
            self.by_type[r.type].append(r)

    def t(self, *types) -> list[Resource]:
        return [r for t in types for r in self.by_type.get(t, [])]

    def prefix(self, *prefixes) -> list[Resource]:
        return [r for r in self.res if r.type.startswith(prefixes)]

    def related(self, *ids) -> list[RuleSummary]:
        return [self.rules[i] for i in ids if i in self.rules]


def _topology(c: _Ctx) -> Topology:
    vnets = {r.id.lower(): r for r in c.t(VNET)}
    has_peering_data = any("virtualNetworkPeerings" in r.properties for r in vnets.values())
    nodes: dict[str, VNetNode] = {}
    for vid, r in vnets.items():
        subnets = [s.get("name", "") for s in _get(r.properties, "subnets", default=[]) if isinstance(s, dict)]
        platform = [PLATFORM_SUBNETS[s.lower()] for s in subnets if s.lower() in PLATFORM_SUBNETS]
        nodes[vid] = VNetNode(vid, r.name, r.subscription_name, r.location,
                              list(_get(r.properties, "addressSpace", "addressPrefixes", default=[])),
                              subnets, sorted(set(platform)))

    # Attach deployed platform services to the VNet whose subnet they sit in (more reliable than subnet names).
    services = {FIREWALL: "Azure Firewall", VNET_GW: "VPN/ExpressRoute gateway", BASTION: "Azure Bastion"}
    for rtype, label in services.items():
        for r in c.t(rtype):
            for ipc in _get(r.properties, "ipConfigurations", default=[]):
                vid = _vnet_of(_get(ipc, "properties", "subnet", "id", default=""))
                if vid in nodes and label not in nodes[vid].platform_services:
                    nodes[vid].platform_services.append(label)
                    nodes[vid].platform_services.sort()
    for r in c.t(VNET_GW):
        gtype = _get(r.properties, "gatewayType", default="")
        label = {"vpn": "VPN gateway", "expressroute": "ExpressRoute gateway"}.get(gtype.lower())
        for ipc in _get(r.properties, "ipConfigurations", default=[]):
            vid = _vnet_of(_get(ipc, "properties", "subnet", "id", default=""))
            if vid in nodes and label:
                ps = nodes[vid].platform_services
                if "VPN/ExpressRoute gateway" in ps:
                    ps.remove("VPN/ExpressRoute gateway")
                if label not in ps:
                    ps.append(label)
                    ps.sort()

    # Default routes to a virtual appliance (forced tunnelling through a firewall/NVA)
    nva_tables = {r.id.lower() for r in c.t(ROUTE_TABLE)
                  if any(_get(rt, "properties", "addressPrefix") == "0.0.0.0/0"
                         and str(_get(rt, "properties", "nextHopType", default="")).lower() == "virtualappliance"
                         for rt in _get(r.properties, "routes", default=[]))}
    for vid, r in vnets.items():
        for s in _get(r.properties, "subnets", default=[]):
            if str(_get(s, "properties", "routeTable", "id", default="")).lower() in nva_tables:
                nodes[vid].default_route_to_nva = True

    edges_set: set[frozenset] = set()
    external = set()
    for vid, r in vnets.items():
        for p in _get(r.properties, "virtualNetworkPeerings", default=[]):
            remote = str(_get(p, "properties", "remoteVirtualNetwork", "id", default="")
                         or _get(p, "remoteVirtualNetwork", "id", default="")).lower()
            if not remote:
                continue
            if remote in nodes:
                if remote != vid:
                    edges_set.add(frozenset((vid, remote)))
            else:
                external.add(remote)
    degree = Counter(v for e in edges_set for v in e)

    # Hubs: VNets that host shared platform services or are named as hubs, and have peers;
    # failing that, a VNet peered to 3+ VNets that are otherwise unconnected.
    hubs = [v for v, n in nodes.items() if degree[v] and (n.platform_services or "hub" in n.name.lower())]
    if not hubs:
        for v in nodes:
            peers = [next(iter(e - {v})) for e in edges_set if v in e]
            if len(peers) >= 3 and all(degree[p] == 1 for p in peers):
                hubs.append(v)
    hub_set = set(hubs)
    edges = []
    for e in edges_set:
        a, b = sorted(e)
        kind = ("hub-hub" if a in hub_set and b in hub_set else
                "hub-spoke" if (a in hub_set) != (b in hub_set) else "spoke-spoke")
        edges.append((a, b, kind))
    edges.sort()
    for a, b, kind in edges:
        if kind == "hub-spoke":
            s, h = (b, a) if a in hub_set else (a, b)
            if nodes[s].hub is None:
                nodes[s].hub = h
    spokes = [v for v in nodes if v not in hub_set and nodes[v].hub]
    isolated = [v for v in nodes if not degree[v]]
    for v in hubs:
        nodes[v].role = "hub"
    for v in isolated:
        nodes[v].role = "isolated"
    for v in nodes:
        if v not in hub_set and not nodes[v].hub and degree[v]:
            nodes[v].role = "peer"

    onprem = []
    for r in c.t(VNET_GW):
        gtype = str(_get(r.properties, "gatewayType", default="")).lower()
        for ipc in _get(r.properties, "ipConfigurations", default=[]):
            vid = _vnet_of(_get(ipc, "properties", "subnet", "id", default=""))
            if vid in nodes:
                onprem.append({"hub": vid, "type": "ExpressRoute" if gtype == "expressroute" else "VPN",
                               "name": r.name})

    vwan = bool(c.t("microsoft.network/virtualwans", "microsoft.network/virtualhubs"))
    spoke_spoke = [e for e in edges if e[2] == "spoke-spoke"]
    if vwan:
        pattern, conf = "Azure Virtual WAN (Microsoft-managed hub-and-spoke)", "High"
    elif not nodes:
        pattern, conf = "No virtual networks (services use public endpoints)", "High" if c.has_props else "Medium"
    elif not has_peering_data:
        hubbish = [n for n in nodes.values() if "hub" in n.name.lower()] or (c.t(FIREWALL) and len(nodes) > 1)
        pattern = "Likely hub-and-spoke (inferred from naming)" if hubbish else "Undetermined (no peering data)"
        conf = "Low"
    elif hubs:
        regions = {nodes[h].location for h in hubs}
        base = "Multi-region hub-and-spoke" if len(regions) > 1 else "Hub-and-spoke"
        exceptions = bool(isolated or spoke_spoke)
        pattern = base + (" with exceptions" if exceptions else "")
        conf = "High"
    elif edges and len(nodes) >= 3 and len(edges) == len(nodes) * (len(nodes) - 1) // 2:
        pattern, conf = "Full mesh peering", "High"
    elif edges:
        pattern, conf = "Ad-hoc peering (no central hub)", "High"
    elif len(nodes) == 1:
        pattern, conf = "Single virtual network", "High"
    else:
        pattern, conf = "Isolated virtual networks (no peering)", "High"
    return Topology(pattern, conf, nodes, edges, hubs, spokes, isolated, len(external), onprem,
                    has_peering_data, vwan)


def _network_dimension(c: _Ctx, t: Topology) -> ArchDimension:
    d = ArchDimension("network", "Network topology", t.pattern, t.confidence)
    n = t.nodes
    if not n:
        d.summary.append("No virtual networks were found. Workloads reach each other and their users over public "
                         "endpoints, which is typical of PaaS-only or early-stage environments.")
        d.considerations.append("If private connectivity is required later, introduce a hub-and-spoke or Virtual "
                                "WAN topology before workloads multiply, as retrofitting it is costly.")
        return d
    if not t.has_peering_data:
        d.summary.append(
            f"There are {_n(len(n), 'virtual network')}, but the export does not include peering configuration, so "
            "the topology cannot be confirmed. "
            + ("Hub-style naming and a shared firewall suggest a hub-and-spoke design." if "hub-and-spoke" in
               t.pattern.lower() else ""))
        d.considerations.append("Ask for a Resource Graph export that includes the properties column to confirm "
                                "the topology.")
        return d
    hubs = [n[h] for h in t.hubs]
    if hubs:
        d.summary.append(
            f"The network is built around {_n(len(hubs), 'hub virtual network')} "
            f"({_join(f'{h.name} in {h.location}' for h in hubs)}), which "
            f"{'hosts' if len(hubs) == 1 else 'host'} shared services: "
            f"{_join(sorted({s for h in hubs for s in h.platform_services})) or 'no dedicated platform services'}. "
            f"{_n(len(t.spokes), 'spoke network')} peer to the hubs and carry the workloads. This is the "
            "hub-and-spoke pattern recommended in the Azure landing zone reference architecture: shared "
            "connectivity, inspection and management access are centralised in the hub, and workloads are "
            "isolated in spokes.")
        hub_hub = [e for e in t.edges if e[2] == "hub-hub"]
        if hub_hub:
            d.summary.append("The regional hubs are peered with each other (global VNet peering), giving "
                             "cross-region connectivity between spokes through their hubs.")
        elif len(hubs) > 1:
            d.summary.append("The regional hubs are not peered with each other, so spokes in different regions "
                             "cannot reach each other privately.")
    elif t.edges:
        d.summary.append(f"The {_n(len(n), 'virtual network')} are peered directly with one another without a "
                         "central hub. This works at small scale but each new network needs peerings to many "
                         "others, and there is no single point to inspect traffic.")
    else:
        d.summary.append(f"The {_n(len(n), 'virtual network')} are not peered, so each is a separate island. "
                         "Resources in different networks can only talk over public endpoints.")

    for h in hubs:
        spokes = [n[s] for s in t.spokes if n[s].hub == h.id]
        d.evidence.append(f"Hub {h.name} ({h.location}, {h.subscription}): address space "
                          f"{', '.join(h.address_space) or 'not recorded'}; services: "
                          f"{_join(h.platform_services) or 'none detected'}; spokes: "
                          f"{_join(s.name for s in spokes) or 'none'}.")
    forced = [n[s].name for s in t.spokes if n[s].default_route_to_nva]
    not_forced = [n[s].name for s in t.spokes if not n[s].default_route_to_nva]
    if t.spokes:
        d.evidence.append(
            (f"Spokes {_join(forced)} send Internet-bound traffic to the hub firewall through a 0.0.0.0/0 route. "
             if forced else "No spoke routes its default traffic through the hub firewall. ")
            + (f"{_join(not_forced)} {'has' if len(not_forced) == 1 else 'have'} no such route, so "
               f"{'its' if len(not_forced) == 1 else 'their'} outbound traffic bypasses central inspection."
               if not_forced else ""))
    spoke_spoke = [e for e in t.edges if e[2] == "spoke-spoke"]
    for a, b, _ in spoke_spoke:
        d.evidence.append(f"{n[a].name} ({n[a].subscription}) is peered directly with {n[b].name} "
                          f"({n[b].subscription}), bypassing the hub.")
    for v in t.isolated:
        d.evidence.append(f"{n[v].name} ({n[v].location}, {n[v].subscription}) is not peered to any network.")
    if t.external_peers:
        d.evidence.append(f"{_n(t.external_peers, 'peering')} point to networks outside this export "
                          "(another subscription or tenant).")

    if spoke_spoke:
        envs = {n[x].subscription for e in spoke_spoke for x in e[:2]}
        d.considerations.append(
            "Remove direct spoke-to-spoke peerings and route that traffic through the hub firewall"
            + (", particularly between production and non-production (" + _join(sorted(envs)) + ")"
               if len(envs) > 1 else "") + ".")
    if not_forced and any(n[h].platform_services and "Azure Firewall" in n[h].platform_services for h in t.hubs):
        d.considerations.append(f"Add a user-defined route (0.0.0.0/0 to the hub firewall) to {_join(not_forced)} "
                                "so all egress is inspected.")
    if t.isolated:
        d.considerations.append(f"Decide whether {_join(n[v].name for v in t.isolated)} should join the hub or "
                                "be removed; unmanaged islands tend to accumulate public exposure.")
    if hubs and len(t.spokes) > 15:
        d.considerations.append("With many spokes, consider Azure Virtual WAN or Azure Virtual Network Manager to "
                                "manage peering and routing at scale.")
    if not d.considerations and hubs:
        d.considerations.append("The topology aligns with the reference architecture; keep new workloads in spokes "
                                "and route their traffic through the hub.")

    d.tables.append(("Virtual networks", ["VNet", "Role", "Subscription", "Region", "Address space",
                                          "Peered with", "Default route via firewall"],
                     [(v.name, v.role.capitalize(), v.subscription, v.location, ", ".join(v.address_space),
                       ", ".join(sorted(n[x].name for e in t.edges for x in e[:2] if v.id in e[:2] and x != v.id))
                       or "-", "Yes" if v.default_route_to_nva else ("-" if v.role == "hub" else "No"))
                      for v in sorted(n.values(), key=lambda v: ({"hub": 0, "spoke": 1, "peer": 2}.get(v.role, 3),
                                                                 v.name))],
                     [2.9, 1.5, 2.9, 2.1, 2.1, 3.4, 1.8]))
    d.related = c.related("SEC-007")
    return d


def _hybrid_dimension(c: _Ctx, t: Topology) -> ArchDimension:
    gws = c.t(VNET_GW)
    conns = c.t("microsoft.network/connections")
    lngs = c.t("microsoft.network/localnetworkgateways")
    ercs = c.t("microsoft.network/expressroutecircuits")
    vpn = [g for g in gws if str(_get(g.properties, "gatewayType", default="")).lower() == "vpn"]
    er = [g for g in gws if str(_get(g.properties, "gatewayType", default="")).lower() == "expressroute"] + ercs
    if er and (vpn or lngs):
        pattern = "ExpressRoute with VPN"
    elif er:
        pattern = "ExpressRoute private connectivity"
    elif vpn or lngs:
        pattern = "Site-to-site VPN to on-premises"
    elif gws:
        pattern = "Gateway present (type not recorded)"
    else:
        pattern = "Cloud-only (no hybrid connectivity found)"
    conf = "High" if (c.has_props or not gws) else "Medium"
    d = ArchDimension("hybrid", "Hybrid connectivity", pattern, conf)
    if not (gws or lngs or ercs):
        d.summary.append("No VPN or ExpressRoute gateways were found. Either the environment is cloud-only, or "
                         "connectivity to on-premises is provided from subscriptions outside this export.")
        d.considerations.append("Confirm whether on-premises connectivity is required; if so, place the gateway "
                                "in the connectivity hub so all spokes can share it.")
        return d
    for g in gws:
        vid = next((_vnet_of(_get(i, "properties", "subnet", "id", default=""))
                    for i in _get(g.properties, "ipConfigurations", default=[])), "")
        where = t.nodes[vid].name if vid in t.nodes else "an unknown network"
        zonal = "zone-redundant" if (g.zones or str(g.sku.get("name", "")).upper().endswith("AZ")) else "not zone-redundant"
        aa = "active-active" if _get(g.properties, "activeActive") else "active-standby"
        d.evidence.append(f"{g.name}: {_get(g.properties, 'gatewayType', default='gateway')} gateway, SKU "
                          f"{g.sku.get('name', 'unknown')}, {zonal}, {aa}, deployed in {where}.")
    for ln in lngs:
        pref = _get(ln.properties, "localNetworkAddressSpace", "addressPrefixes", default=[])
        d.evidence.append(f"{ln.name}: on-premises site at {_get(ln.properties, 'gatewayIpAddress', default='?')} "
                          f"advertising {', '.join(pref) or 'unknown ranges'}.")
    for cn in conns:
        d.evidence.append(f"{cn.name}: {_get(cn.properties, 'connectionType', default='connection')} connection, "
                          f"status {_get(cn.properties, 'connectionStatus', default='not recorded')}.")
    hub_gw = [o for o in t.onprem if o["hub"] in t.hubs]
    how = {"ExpressRoute with VPN": "ExpressRoute, with a VPN", "ExpressRoute private connectivity": "ExpressRoute",
           "Site-to-site VPN to on-premises": "a site-to-site VPN"}.get(pattern, "a network gateway")
    d.summary.append(
        f"The environment connects to on-premises through {how}"
        + (f", terminated in the hub network {t.nodes[hub_gw[0]['hub']].name}. Spokes use the hub gateway through "
           "gateway transit, which is the recommended shared-gateway design." if hub_gw else ".")
    )
    regions_with_hubs = {t.nodes[h].location for h in t.hubs}
    regions_with_gw = {t.nodes[o["hub"]].location for o in t.onprem}
    if len(regions_with_hubs) > 1 and regions_with_gw and regions_with_gw != regions_with_hubs:
        d.considerations.append(
            f"Only {_join(sorted(regions_with_gw))} has a gateway; spokes in "
            f"{_join(sorted(regions_with_hubs - regions_with_gw))} reach on-premises across regions, and lose "
            "on-premises connectivity if that region is unavailable. Add a gateway in each regional hub.")
    if vpn and not er:
        d.considerations.append("A single VPN tunnel runs over the Internet; for production workloads with "
                                "latency or bandwidth needs, consider ExpressRoute with VPN as backup.")
    if any(not _get(g.properties, "activeActive") for g in vpn):
        d.considerations.append("Configure VPN gateways as active-active with two on-premises devices to remove a "
                                "single point of failure.")
    return d


def _traffic_dimension(c: _Ctx, t: Topology) -> ArchDimension:
    fws = c.t(FIREWALL)
    appgws = c.t(APPGW)
    waf = [g for g in appgws if "waf" in str(_get(g.properties, "sku", "tier", default=g.sku.get("tier", ""))).lower()
           or _get(g.properties, "webApplicationFirewallConfiguration", "enabled")]
    fds = [r for r in c.t(*FRONTDOOR) if r.type == "microsoft.network/frontdoors"
           or "frontdoor" in str(r.sku.get("name", "")).lower()]
    bastion = c.t(BASTION)
    pes = c.t(PE)
    ddos = c.t("microsoft.network/ddosprotectionplans")
    nsg_open = c.rules.get("SEC-007")
    parts = []
    if fws:
        parts.append("central firewall")
    if waf or fds:
        parts.append("WAF-protected ingress")
    if pes:
        parts.append("private endpoints for PaaS")
    if bastion:
        parts.append("Bastion for admin access")
    pattern = ("Defence in depth (" + ", ".join(parts) + ")") if len(parts) >= 3 else (
        ("Partial perimeter controls (" + ", ".join(parts) + ")") if parts else "No central perimeter controls")
    d = ArchDimension("traffic", "Traffic flow and perimeter security", pattern,
                      "High" if c.has_props else "Medium")
    # Ingress
    ingress = []
    if fds:
        ingress.append(f"Azure Front Door ({_join(r.name for r in fds)})")
    if appgws:
        ingress.append(f"{_n(len(appgws), 'Application Gateway')} ({_join(g.name for g in appgws)}; "
                       + ("WAF enabled)" if len(waf) == len(appgws) else f"{len(waf)} with WAF)"))
    lbs = c.t("microsoft.network/loadbalancers")
    if lbs:
        ingress.append(f"{_n(len(lbs), 'load balancer')}")
    d.summary.append("Inbound: " + (("traffic enters through " + _join(ingress) + ".") if ingress else
                                    "no Application Gateway, Front Door or load balancer was found, so applications "
                                    "are reached directly on their own public endpoints."))
    # Egress
    forced = [t.nodes[s].name for s in t.spokes if t.nodes[s].default_route_to_nva]
    d.summary.append("Outbound and east-west: " + (
        f"{_n(len(fws), 'Azure Firewall')} ({_join(f.name for f in fws)}) "
        + (f"inspect traffic; {_n(len(forced), 'spoke')} force Internet-bound traffic through them."
           if forced else "are deployed, but no spoke routes its traffic through them, so they inspect little.")
        if fws else "no firewall or network virtual appliance was found; outbound traffic leaves directly."))
    # Management and PaaS
    d.summary.append("Administration: " + (
        f"Azure Bastion ({_join(b.name for b in bastion)}) provides browser-based RDP/SSH without public IPs on "
        "VMs." if bastion else "no Azure Bastion was found.")
        + (" However, some network security groups still allow RDP or SSH from the Internet." if nsg_open else ""))
    paas_public = [r.name for r in c.res if _get(r.properties, "publicNetworkAccess") == "Enabled"]
    d.summary.append("PaaS access: " + (
        f"{_n(len(pes), 'private endpoint')} connect PaaS services privately "
        f"({_join(sorted({_get(p.properties, 'privateLinkServiceConnections', default=[{}])[0].get('properties', {}).get('privateLinkServiceId', '').rsplit('/', 1)[-1] for p in pes} - {''}))}). "
        if pes else "no private endpoints were found. ")
        + (f"{_n(len(paas_public), 'service')} still accept public network traffic." if paas_public else ""))
    d.evidence += [f"Web application firewall: {_join(g.name for g in waf) or 'none'}.",
                   f"DDoS Network Protection plan: {_join(r.name for r in ddos) or 'none'}."]
    if nsg_open:
        d.considerations.append("Remove Internet-sourced RDP/SSH rules now that Bastion is available."
                                if bastion else "Deploy Azure Bastion and remove Internet-sourced RDP/SSH rules.")
    if paas_public:
        d.considerations.append(f"Extend private endpoints to the remaining PaaS services and disable their public "
                                f"network access ({_join(paas_public[:6])}{' …' if len(paas_public) > 6 else ''}).")
    if not ddos and (appgws or c.t("microsoft.network/publicipaddresses")):
        d.considerations.append("Consider DDoS Network Protection for virtual networks with Internet-facing "
                                "workloads.")
    if fws and not forced:
        d.considerations.append("Route spoke traffic through the firewall with user-defined routes.")
    d.related = c.related("SEC-007", "SEC-005", "SEC-002", "SEC-006")
    return d


def _landing_zone_dimension(c: _Ctx) -> ArchDimension:
    by_sub: dict[str, list[Resource]] = defaultdict(list)
    for r in c.res:
        by_sub[r.subscription_name].append(r)
    roles = {}
    for sub, items in by_sub.items():
        name = sub.lower()
        net = sum(1 for r in items if r.type.startswith("microsoft.network/"))
        mon = sum(1 for r in items if r.type.startswith(("microsoft.operationalinsights/", "microsoft.automation/",
                                                          "microsoft.insights/", "microsoft.recoveryservices/")))
        envs = Counter(v.lower() for r in items for k, v in r.tags.items() if k.lower() in ("environment", "env"))
        env = envs.most_common(1)[0][0] if envs else ""
        if any(k in name for k in ("connectivity", "network", "hub")) or net >= 0.7 * len(items):
            roles[sub] = "Platform: connectivity"
        elif any(k in name for k in ("management", "mgmt", "monitor")) or mon >= 0.6 * len(items):
            roles[sub] = "Platform: management"
        elif "identity" in name:
            roles[sub] = "Platform: identity"
        elif any(k in name for k in ("nonprod", "non-prod", "dev", "test", "uat", "qa")) or env in (
                "dev", "test", "uat", "qa", "nonprod"):
            roles[sub] = "Workload: non-production"
        elif "prod" in name or env in ("prod", "production", "prd"):
            roles[sub] = "Workload: production"
        elif "sandbox" in name:
            roles[sub] = "Sandbox"
        else:
            roles[sub] = "Workload"
    platform = [s for s, r in roles.items() if r.startswith("Platform")]
    workload = [s for s, r in roles.items() if r.startswith("Workload")]
    env_split = {"Workload: production", "Workload: non-production"} <= set(roles.values())
    if len(by_sub) == 1:
        pattern = "Single subscription"
    elif platform and env_split:
        pattern = "Landing-zone style: platform and workload subscriptions, split by environment"
    elif platform:
        pattern = "Landing-zone style: platform and workload subscriptions"
    elif env_split:
        pattern = "Environment-based subscriptions (no platform subscriptions)"
    else:
        pattern = "Multiple subscriptions without a clear separation model"
    d = ArchDimension("landing_zone", "Subscription and landing zone model", pattern, "Medium")
    d.summary.append(
        f"The {_n(len(by_sub), 'subscription')} in scope appear to play these roles: "
        + _join(f"{s} ({roles[s].lower()})" for s in sorted(by_sub)) + ". Roles are inferred from subscription "
        "names, environment tags and the resources each contains.")
    if platform:
        d.summary.append("Separating shared platform services (connectivity, management) from application "
                         "workloads follows the Azure landing zone model in the Cloud Adoption Framework, where "
                         "platform teams own the hub and workload teams own their subscriptions.")
    d.tables.append(("Subscription roles", ["Subscription", "Inferred role", "Resources", "Main resource types"],
                     [(s, roles[s], len(by_sub[s]),
                       ", ".join(f"{friendly_type(t)} ({n})" for t, n in
                                 Counter(r.type for r in by_sub[s]).most_common(3)))
                      for s in sorted(by_sub, key=lambda s: roles[s])], [3.2, 3.3, 2.1, 5.6]))
    if not any(r == "Platform: management" for r in roles.values()):
        laws = {r.subscription_name for r in c.t("microsoft.operationalinsights/workspaces")}
        d.considerations.append(
            "There is no dedicated management subscription"
            + (f"; Log Analytics workspaces sit in {_join(sorted(laws))}" if laws else "")
            + ". The landing zone model places central logging, automation and backup in a management "
              "subscription.")
    if not any(r == "Platform: identity" for r in roles.values()):
        d.considerations.append("No identity subscription was found. If domain controllers or identity services "
                                "run in Azure, isolate them in a dedicated identity subscription.")
    if len(workload) == 1 and len(by_sub) > 1:
        d.considerations.append("All workloads share one subscription; separate production and non-production to "
                                "contain blast radius and apply different policies.")
    d.considerations.append("Management groups, Azure Policy assignments and RBAC are not part of a resource "
                            "inventory; review them separately to confirm the landing zone governance.")
    return d


def _hosting_dimension(c: _Ctx) -> ArchDimension:
    vms = c.t("microsoft.compute/virtualmachines")
    vmss = c.t("microsoft.compute/virtualmachinescalesets")
    aks = c.t("microsoft.containerservice/managedclusters")
    aca = c.prefix("microsoft.app/")
    sites = c.t("microsoft.web/sites")
    funcs = [s for s in sites if "functionapp" in (s.kind or "").lower()]
    webapps = [s for s in sites if s not in funcs]
    logic = c.t("microsoft.logic/workflows")
    sql = c.prefix("microsoft.sql/", "microsoft.dbfor", "microsoft.documentdb/", "microsoft.cache/")
    integ = c.prefix("microsoft.apimanagement/", "microsoft.servicebus/", "microsoft.eventhub/",
                     "microsoft.eventgrid/")
    styles = []
    if vms or vmss:
        styles.append("IaaS virtual machines")
    if webapps:
        styles.append("App Service web apps")
    if aks or aca:
        styles.append("containers")
    if funcs or logic:
        styles.append("serverless")
    iaas = len(vms) + len(vmss)
    paas = len(webapps) + len(funcs) + len(sql) + len(aks) + len(aca)
    tiers = {"presentation": bool(c.t(APPGW) or webapps or c.t(*FRONTDOOR)),
             "application": bool(vms or vmss or aks or webapps or funcs),
             "data": bool(sql or c.t("microsoft.storage/storageaccounts"))}
    if not styles:
        pattern = "No compute workloads found"
    elif all(tiers.values()) and len(styles) > 1:
        pattern = "Hybrid IaaS/PaaS, three-tier (web, application, data)"
    elif all(tiers.values()):
        pattern = f"Three-tier on {styles[0]}"
    elif len(styles) > 1:
        pattern = "Mixed " + _join(styles)
    else:
        pattern = styles[0].capitalize()
    d = ArchDimension("hosting", "Application hosting architecture", pattern, "Medium")
    d.summary.append(
        f"Workloads run on {_join(styles) or 'no compute services'}: {_n(iaas, 'VM-based resource')} and "
        f"{_n(paas, 'managed (PaaS) service')}. "
        + ("VMs make up the majority, so the estate is primarily IaaS; much of the operating system patching, "
           "scaling and availability work remains with the customer." if iaas > paas else
           "Managed services make up the majority, so Azure handles much of the platform operation."))
    d.summary.append(
        "Tiers identified: "
        + _join([f"presentation ({_join([x for x in ('Application Gateway' if c.t(APPGW) else '', 'App Service' if webapps else '', 'Front Door' if c.t(*FRONTDOOR) else '') if x])})" if tiers["presentation"] else "",
                 f"application ({_join([x for x in ('VMs' if vms else '', 'scale sets' if vmss else '', 'AKS' if aks else '', 'App Service' if webapps else '', 'Functions' if funcs else '') if x])})" if tiers["application"] else "",
                 f"data ({_join(sorted({friendly_type(r.type) for r in sql}) or ['Storage accounts'])})" if tiers["data"] else ""])
        + ".")
    if integ:
        d.summary.append(f"Integration services: {_join(sorted({friendly_type(r.type) for r in integ}))}.")
    d.evidence.append(f"Virtual machines: {len(vms)}; scale sets: {len(vmss)}; AKS clusters: {len(aks)}; "
                      f"web apps: {len(webapps)}; function apps: {len(funcs)}; databases and caches: {len(sql)}.")
    if vms and not vmss:
        d.considerations.append("No VM scale sets are used, so VM capacity does not scale automatically. Stateless "
                                "tiers are good candidates for scale sets or App Service.")
    if iaas > paas:
        d.considerations.append("Review VM-hosted components for replatforming to PaaS (App Service, Azure SQL, "
                                "AKS) to reduce operational overhead.")
    d.related = c.related("OPS-001", "COST-003", "COST-005")
    return d


def _resilience_dimension(c: _Ctx) -> ArchDimension:
    vms = c.t("microsoft.compute/virtualmachines")
    prod = [r for r in c.res if any(v.lower() in ("prod", "production", "prd") for k, v in r.tags.items()
                                    if k.lower() in ("environment", "env")) or "prod" in r.subscription_name.lower()
            and "nonprod" not in r.subscription_name.lower().replace("-", "")]
    scope = prod or c.res
    regions = Counter(r.location for r in scope if r.location and r.location != "global")
    pairs = sorted({tuple(sorted((a, REGION_PAIRS[a]))) for a in regions if REGION_PAIRS.get(a) in regions})
    zonal_vms = sum(1 for v in vms if v.zones)
    st = c.t("microsoft.storage/storageaccounts")
    geo = sum(1 for s in st if any(x in str(s.sku.get("name", "")).upper() for x in ("GRS", "GZRS")))
    zrs = sum(1 for s in st if "ZRS" in str(s.sku.get("name", "")).upper())
    dbs = [d_ for d_ in c.t("microsoft.sql/servers/databases") if not d_.name.endswith("/master")]
    zr_db = sum(1 for x in dbs if _get(x.properties, "zoneRedundant"))
    vaults = c.t("microsoft.recoveryservices/vaults", "microsoft.dataprotection/backupvaults")
    asr = [v for v in vaults if _get(v.properties, "replicationPolicies")]
    who = "Production" if prod else "The estate"
    if len(regions) <= 1:
        pattern = ("Single region, zone-resilient" if zonal_vms and zonal_vms >= len(vms) / 2 else
                   "Single region without zone redundancy")
    elif pairs:
        pattern = "Multi-region across Azure region pairs (DR-capable)"
    else:
        pattern = "Multi-region, but not in Azure region pairs"
    if len(regions) > 1 and not asr:
        pattern += "; no DR replication evidence"
    d = ArchDimension("resilience", "Resilience and disaster recovery", pattern,
                      "Medium" if c.has_props else "Low")
    d.summary.append(
        f"{who} runs in {_n(len(regions), 'region')} ({', '.join(f'{k}: {v}' for k, v in regions.most_common())} "
        "resources). "
        + (f"{_join(f'{a}/{b}' for a, b in pairs)} {'is an Azure region pair' if len(pairs) == 1 else 'are Azure region pairs'}, "
           "which Azure recovers in priority order and updates one at a time. " if pairs else
           "None of these regions are paired with each other. " if len(regions) > 1 else "")
        + "Running in several regions only provides disaster recovery if data is replicated and workloads can "
          "fail over; the inventory shows the building blocks, not a tested failover plan.")
    d.summary.append(
        f"Within regions: {zonal_vms} of {len(vms)} VMs use availability zones, {zrs} of {len(st)} storage accounts "
        f"replicate across zones and {geo} replicate to a paired region, and {zr_db} of {len(dbs)} SQL databases "
        f"{'is' if zr_db == 1 else 'are'} zone redundant." if c.has_props else
        "Zone placement and replication settings are not in the export.")
    d.summary.append(
        f"Backup and site recovery: {_n(len(vaults), 'vault')} found"
        + (f" ({_join(v.name for v in vaults)})." if vaults else
           ", so there is no evidence of Azure Backup or Azure Site Recovery protecting these workloads."))
    if not vaults:
        d.considerations.append("Deploy Recovery Services vaults and back up VMs and databases; define RPO/RTO per "
                                "workload.")
    if len(regions) > 1 and not asr:
        d.considerations.append("Decide the DR strategy per workload (active/active, active/passive or "
                                "backup-and-restore), then replicate data and test failover.")
    if vms and zonal_vms < len(vms):
        d.considerations.append("Place production VMs across availability zones (or use zone-redundant scale sets) "
                                "to survive a datacenter failure.")
    d.related = c.related("REL-001", "REL-003", "REL-004")
    return d


def _operations_dimension(c: _Ctx) -> ArchDimension:
    laws = c.t("microsoft.operationalinsights/workspaces")
    autos = c.t("microsoft.automation/automationaccounts")
    subs = {r.subscription_name for r in c.res}
    law_subs = {w.subscription_name for w in laws}
    if not laws:
        pattern = "No central logging found"
    elif len(laws) == 1:
        pattern = "Centralised logging (single workspace)"
    elif len(law_subs) == len(laws):
        pattern = "Federated logging (one workspace per subscription)"
    else:
        pattern = f"Multiple workspaces ({len(laws)})"
    d = ArchDimension("operations", "Operations and monitoring", pattern, "Medium")
    if laws:
        d.summary.append(
            f"Logs go to {_n(len(laws), 'Log Analytics workspace')} ({_join(f'{w.name} in {w.subscription_name}' for w in laws)}). "
            + ("Each subscription keeps its own workspace, which simplifies access control per environment but "
               "makes cross-environment queries and a single security view (Microsoft Sentinel) harder."
               if len(laws) > 1 else "A single workspace gives one place to query and alert across the estate."))
        uncovered = subs - law_subs
        if uncovered and len(laws) > 1:
            d.summary.append(f"{_join(sorted(uncovered))} {'has' if len(uncovered) == 1 else 'have'} no workspace of "
                             f"{'its' if len(uncovered) == 1 else 'their'} own and must send logs elsewhere.")
    else:
        d.summary.append("No Log Analytics workspace was found, so there is no evidence of centralised logging.")
    d.summary.append(f"Automation accounts: {len(autos)}. Diagnostic settings, alert rules and Azure Monitor agent "
                     "coverage are not part of a resource inventory and need a separate review.")
    if len(laws) > 1:
        d.considerations.append("The landing zone model recommends a central workspace in the management "
                                "subscription, with resource-context access for workload teams.")
    d.related = c.related("OPS-002")
    return d


def analyse(a: Assessment) -> Architecture:
    c = _Ctx(a)
    topo = _topology(c)
    dims = [_network_dimension(c, topo), _hybrid_dimension(c, topo), _traffic_dimension(c, topo),
            _landing_zone_dimension(c), _hosting_dimension(c), _resilience_dimension(c), _operations_dimension(c)]
    return Architecture(topo, dims)
