"""Deterministic demo inventory so the reports can be tried without an Azure tenant."""
from __future__ import annotations

import random

from ..models import Inventory, Resource

SUBS = {
    "11111111-1111-1111-1111-111111111111": "Contoso-Production",
    "22222222-2222-2222-2222-222222222222": "Contoso-NonProduction",
    "33333333-3333-3333-3333-333333333333": "Contoso-Connectivity",
}
REGIONS = ["eastus", "eastus", "westeurope", "westeurope", "centralindia", "uksouth"]
ENVS = {"Contoso-Production": "prod", "Contoso-NonProduction": "dev", "Contoso-Connectivity": "shared"}


def _rid(sub, rg, provider, name):
    return f"/subscriptions/{sub}/resourceGroups/{rg}/providers/{provider}/{name}"


def build(seed: int = 7) -> Inventory:
    rnd = random.Random(seed)
    resources: list[Resource] = []

    def add(sub, rg, rtype, name, *, location=None, sku=None, kind="", props=None, zones=None, tagged=None):
        env = ENVS[SUBS[sub]]
        tags = {"environment": env, "owner": rnd.choice(["app-team", "platform", "data-team"]),
                "costCenter": rnd.choice(["CC100", "CC200", "CC300"])}
        if tagged is None:
            tagged = rnd.random() > 0.35
        if not tagged:
            tags = {k: v for k, v in tags.items() if rnd.random() > 0.6}
        resources.append(Resource(
            id=_rid(sub, rg, rtype, name), name=name, type=rtype.lower(),
            location=location or rnd.choice(REGIONS), resource_group=rg,
            subscription_id=sub, subscription_name=SUBS[sub], kind=kind, sku=sku or {},
            tags=tags, zones=zones or [], properties=props or {},
        ))

    prod, nonprod, conn = list(SUBS)

    for sub, prefix, count in [(prod, "prd", 14), (nonprod, "dev", 10)]:
        for i in range(count):
            rg = f"rg-{prefix}-app{i % 4 + 1:02d}"
            loc = rnd.choice(REGIONS)
            power = rnd.choices(["PowerState/running", "PowerState/deallocated", "PowerState/stopped"],
                                [0.78, 0.14, 0.08])[0]
            size = rnd.choice(["Standard_D4s_v5", "Standard_D2s_v5", "Standard_B2ms", "Standard_E8s_v5",
                               "Standard_A2_v2", "Standard_DS2_v2"])
            zoned = sub == prod and rnd.random() > 0.45
            os_type = rnd.choice(["Linux", "Linux", "Windows"])
            add(sub, rg, "Microsoft.Compute/virtualMachines", f"vm-{prefix}-{i:03d}", location=loc,
                zones=[str(rnd.randint(1, 3))] if zoned else [],
                props={"hardwareProfile": {"vmSize": size},
                       "storageProfile": {"osDisk": {"osType": os_type, "managedDisk": {"id": "x"}}},
                       "extended": {"instanceView": {"powerState": {"code": power}}},
                       "availabilitySet": None})
            add(sub, rg, "Microsoft.Compute/disks", f"vm-{prefix}-{i:03d}_OsDisk", location=loc,
                sku={"name": "Premium_LRS"}, props={"diskState": "Attached", "diskSizeGB": 128})
            add(sub, rg, "Microsoft.Network/networkInterfaces", f"nic-{prefix}-{i:03d}", location=loc,
                props={"virtualMachine": {"id": "x"}})
        for i in range(rnd.randint(3, 6)):
            add(sub, f"rg-{prefix}-app01", "Microsoft.Compute/disks", f"disk-{prefix}-orphan-{i}",
                sku={"name": rnd.choice(["Premium_LRS", "StandardSSD_LRS"])},
                props={"diskState": "Unattached", "diskSizeGB": rnd.choice([64, 128, 256, 512])})

        for i in range(5):
            add(sub, f"rg-{prefix}-data", "Microsoft.Storage/storageAccounts", f"st{prefix}data{i:02d}",
                kind="StorageV2", sku={"name": rnd.choice(["Standard_LRS", "Standard_GRS", "Standard_ZRS"])},
                props={"supportsHttpsTrafficOnly": rnd.random() > 0.15,
                       "allowBlobPublicAccess": rnd.random() > 0.6,
                       "minimumTlsVersion": rnd.choice(["TLS1_2", "TLS1_2", "TLS1_0"]),
                       "publicNetworkAccess": "Enabled"})
        for i in range(2):
            add(sub, f"rg-{prefix}-sec", "Microsoft.KeyVault/vaults", f"kv-{prefix}-{i:02d}",
                props={"enableSoftDelete": True, "enablePurgeProtection": rnd.random() > 0.5,
                       "publicNetworkAccess": "Enabled", "sku": {"name": "standard"}})
        add(sub, f"rg-{prefix}-data", "Microsoft.Sql/servers", f"sql-{prefix}-01",
            props={"publicNetworkAccess": rnd.choice(["Enabled", "Disabled"]), "minimalTlsVersion": "1.2"})
        for i in range(3):
            add(sub, f"rg-{prefix}-data", "Microsoft.Sql/servers/databases", f"sql-{prefix}-01/db{i}",
                sku={"name": rnd.choice(["GP_Gen5_2", "S1", "Basic"])},
                props={"zoneRedundant": rnd.random() > 0.6})
        for i in range(2):
            add(sub, f"rg-{prefix}-web", "Microsoft.Web/serverFarms", f"asp-{prefix}-{i:02d}",
                sku={"name": rnd.choice(["P1v3", "S1", "B1"])},
                props={"numberOfSites": rnd.choice([0, 2, 3])})
        for i in range(4):
            add(sub, f"rg-{prefix}-web", "Microsoft.Web/sites", f"app-{prefix}-{i:02d}", kind="app",
                props={"httpsOnly": rnd.random() > 0.3, "siteConfig": {"minTlsVersion": "1.2"}})
        add(sub, f"rg-{prefix}-mon", "Microsoft.OperationalInsights/workspaces", f"log-{prefix}-01",
            props={"retentionInDays": 30})

    # Connectivity hub
    for region in ["eastus", "westeurope"]:
        add(conn, f"rg-hub-{region}", "Microsoft.Network/virtualNetworks", f"vnet-hub-{region}", location=region,
            tagged=True)
        add(conn, f"rg-hub-{region}", "Microsoft.Network/azureFirewalls", f"afw-hub-{region}", location=region,
            tagged=True, zones=["1", "2", "3"])
        add(conn, f"rg-hub-{region}", "Microsoft.Network/networkSecurityGroups", f"nsg-mgmt-{region}",
            location=region, props={"securityRules": [
                {"name": "allow-rdp", "properties": {"direction": "Inbound", "access": "Allow",
                                                    "sourceAddressPrefix": "*", "destinationPortRange": "3389"}},
                {"name": "allow-https", "properties": {"direction": "Inbound", "access": "Allow",
                                                      "sourceAddressPrefix": "Internet",
                                                      "destinationPortRange": "443"}},
            ]})
        add(conn, f"rg-hub-{region}", "Microsoft.Network/networkSecurityGroups", f"nsg-app-{region}",
            location=region, props={"securityRules": [
                {"name": "allow-lb", "properties": {"direction": "Inbound", "access": "Allow",
                                                   "sourceAddressPrefix": "AzureLoadBalancer",
                                                   "destinationPortRange": "*"}}]})
        for i in range(3):
            add(conn, f"rg-hub-{region}", "Microsoft.Network/publicIPAddresses", f"pip-{region}-{i}",
                location=region, sku={"name": "Standard"},
                props={"ipConfiguration": {"id": "x"} if i else None})
    add(conn, "rg-legacy", "Microsoft.ClassicCompute/domainNames", "legacy-cloudservice", location="eastus")

    _add_network_architecture(add, resources, prod, nonprod, conn)

    return Inventory(resources=resources, subscriptions=dict(SUBS),
                     tenant_id="00000000-0000-0000-0000-000000000000", source="sample data")


def _vnet_id(sub, rg, name):
    return _rid(sub, rg, "Microsoft.Network/virtualNetworks", name)


def _subnet(vnet_id, name, prefix, nsg_id=None, rt_id=None):
    props = {"addressPrefix": prefix}
    if nsg_id:
        props["networkSecurityGroup"] = {"id": nsg_id}
    if rt_id:
        props["routeTable"] = {"id": rt_id}
    return {"id": f"{vnet_id}/subnets/{name}", "name": name, "properties": props}


def _peering(remote_id, name, *, remote_gateways=False, gateway_transit=False):
    return {"name": name, "properties": {
        "remoteVirtualNetwork": {"id": remote_id}, "peeringState": "Connected",
        "allowForwardedTraffic": True, "useRemoteGateways": remote_gateways,
        "allowGatewayTransit": gateway_transit}}


def _add_network_architecture(add, resources, prod, nonprod, conn):
    """A realistic network layout: two regional hubs with spokes, plus a few deliberate deviations
    (a spoke-to-spoke peering between dev and prod, and an unpeered sandbox VNet)."""
    hub = {r: _vnet_id(conn, f"rg-hub-{r}", f"vnet-hub-{r}") for r in ("eastus", "westeurope")}
    spoke = {
        "prd-eastus": _vnet_id(prod, "rg-prd-network", "vnet-prd-eastus"),
        "prd-westeurope": _vnet_id(prod, "rg-prd-network", "vnet-prd-westeurope"),
        "dev-eastus": _vnet_id(nonprod, "rg-dev-network", "vnet-dev-eastus"),
    }
    sandbox = _vnet_id(nonprod, "rg-dev-sandbox", "vnet-dev-sandbox")
    nsg = lambda sub, rg, n: _rid(sub, rg, "Microsoft.Network/networkSecurityGroups", n)  # noqa: E731
    rt = lambda sub, rg, n: _rid(sub, rg, "Microsoft.Network/routeTables", n)  # noqa: E731

    # Hubs already exist (created above); give them address space, platform subnets and peerings.
    by_id = {r.id: r for r in resources}
    by_id[hub["eastus"]].properties = {
        "addressSpace": {"addressPrefixes": ["10.0.0.0/22"]},
        "subnets": [_subnet(hub["eastus"], "AzureFirewallSubnet", "10.0.0.0/26"),
                    _subnet(hub["eastus"], "GatewaySubnet", "10.0.0.64/27"),
                    _subnet(hub["eastus"], "AzureBastionSubnet", "10.0.0.128/26")],
        "virtualNetworkPeerings": [
            _peering(spoke["prd-eastus"], "hub-to-prd", gateway_transit=True),
            _peering(spoke["dev-eastus"], "hub-to-dev", gateway_transit=True),
            _peering(hub["westeurope"], "hub-eus-to-hub-weu")]}
    by_id[hub["westeurope"]].properties = {
        "addressSpace": {"addressPrefixes": ["10.10.0.0/22"]},
        "subnets": [_subnet(hub["westeurope"], "AzureFirewallSubnet", "10.10.0.0/26")],
        "virtualNetworkPeerings": [
            _peering(spoke["prd-westeurope"], "hub-to-prd"),
            _peering(hub["eastus"], "hub-weu-to-hub-eus")]}
    for region in ("eastus", "westeurope"):
        fw = by_id[_rid(conn, f"rg-hub-{region}", "Microsoft.Network/azureFirewalls", f"afw-hub-{region}")]
        fw.sku = {"name": "AZFW_VNet", "tier": "Premium"}
        fw.properties = {"ipConfigurations": [{"name": "fw-ipconfig", "properties": {
            "subnet": {"id": f"{hub[region]}/subnets/AzureFirewallSubnet"},
            "privateIPAddress": "10.0.0.4" if region == "eastus" else "10.10.0.4"}}]}

    # Spokes
    add(prod, "rg-prd-network", "Microsoft.Network/virtualNetworks", "vnet-prd-eastus", location="eastus", tagged=True,
        props={"addressSpace": {"addressPrefixes": ["10.1.0.0/16"]},
               "subnets": [
                   _subnet(spoke["prd-eastus"], "snet-web", "10.1.1.0/24",
                           nsg(prod, "rg-prd-network", "nsg-prd-web"), rt(prod, "rg-prd-network", "rt-prd-eastus")),
                   _subnet(spoke["prd-eastus"], "snet-app", "10.1.2.0/24",
                           nsg(prod, "rg-prd-network", "nsg-prd-app"), rt(prod, "rg-prd-network", "rt-prd-eastus")),
                   _subnet(spoke["prd-eastus"], "snet-data", "10.1.3.0/24",
                           nsg(prod, "rg-prd-network", "nsg-prd-data"), rt(prod, "rg-prd-network", "rt-prd-eastus")),
                   _subnet(spoke["prd-eastus"], "snet-pe", "10.1.4.0/24")],
               "virtualNetworkPeerings": [_peering(hub["eastus"], "prd-to-hub", remote_gateways=True),
                                          _peering(spoke["dev-eastus"], "prd-to-dev")]})
    add(prod, "rg-prd-network", "Microsoft.Network/virtualNetworks", "vnet-prd-westeurope", location="westeurope",
        tagged=True,
        props={"addressSpace": {"addressPrefixes": ["10.11.0.0/16"]},
               "subnets": [
                   _subnet(spoke["prd-westeurope"], "snet-web", "10.11.1.0/24",
                           nsg(prod, "rg-prd-network", "nsg-prd-web"), rt(prod, "rg-prd-network", "rt-prd-weu")),
                   _subnet(spoke["prd-westeurope"], "snet-app", "10.11.2.0/24",
                           nsg(prod, "rg-prd-network", "nsg-prd-app"), rt(prod, "rg-prd-network", "rt-prd-weu"))],
               "virtualNetworkPeerings": [_peering(hub["westeurope"], "prd-to-hub")]})
    add(nonprod, "rg-dev-network", "Microsoft.Network/virtualNetworks", "vnet-dev-eastus", location="eastus",
        tagged=True,
        props={"addressSpace": {"addressPrefixes": ["10.2.0.0/16"]},
               "subnets": [_subnet(spoke["dev-eastus"], "snet-app", "10.2.1.0/24"),
                           _subnet(spoke["dev-eastus"], "snet-data", "10.2.2.0/24")],
               "virtualNetworkPeerings": [_peering(hub["eastus"], "dev-to-hub", remote_gateways=True),
                                          _peering(spoke["prd-eastus"], "dev-to-prd")]})
    add(nonprod, "rg-dev-sandbox", "Microsoft.Network/virtualNetworks", "vnet-dev-sandbox", location="uksouth",
        props={"addressSpace": {"addressPrefixes": ["10.99.0.0/16"]},
               "subnets": [_subnet(sandbox, "default", "10.99.0.0/24")], "virtualNetworkPeerings": []})

    for n in ("nsg-prd-web", "nsg-prd-app", "nsg-prd-data"):
        add(prod, "rg-prd-network", "Microsoft.Network/networkSecurityGroups", n, location="eastus", tagged=True,
            props={"securityRules": [{"name": "deny-internet-in", "properties": {
                "direction": "Inbound", "access": "Deny", "sourceAddressPrefix": "Internet",
                "destinationPortRange": "*"}}]})
    for name, region, nva in (("rt-prd-eastus", "eastus", "10.0.0.4"), ("rt-prd-weu", "westeurope", "10.10.0.4")):
        add(prod, "rg-prd-network", "Microsoft.Network/routeTables", name, location=region, tagged=True,
            props={"routes": [{"name": "default-via-firewall", "properties": {
                "addressPrefix": "0.0.0.0/0", "nextHopType": "VirtualAppliance", "nextHopIpAddress": nva}}]})

    # Hybrid connectivity: site-to-site VPN from the East US hub to the on-premises datacenter
    gw_id = _rid(conn, "rg-hub-eastus", "Microsoft.Network/virtualNetworkGateways", "vgw-hub-eastus")
    lng_id = _rid(conn, "rg-hub-eastus", "Microsoft.Network/localNetworkGateways", "lng-onprem-dc1")
    add(conn, "rg-hub-eastus", "Microsoft.Network/virtualNetworkGateways", "vgw-hub-eastus", location="eastus",
        tagged=True, zones=["1", "2", "3"], sku={"name": "VpnGw2AZ", "tier": "VpnGw2AZ"},
        props={"gatewayType": "Vpn", "vpnType": "RouteBased", "activeActive": False,
               "ipConfigurations": [{"name": "gw", "properties": {
                   "subnet": {"id": f"{hub['eastus']}/subnets/GatewaySubnet"}}}]})
    add(conn, "rg-hub-eastus", "Microsoft.Network/localNetworkGateways", "lng-onprem-dc1", location="eastus",
        tagged=True, props={"gatewayIpAddress": "203.0.113.10",
                            "localNetworkAddressSpace": {"addressPrefixes": ["192.168.0.0/16"]}})
    add(conn, "rg-hub-eastus", "Microsoft.Network/connections", "cn-hub-eastus-to-dc1", location="eastus",
        tagged=True, props={"connectionType": "IPsec", "virtualNetworkGateway1": {"id": gw_id},
                            "localNetworkGateway2": {"id": lng_id}, "connectionStatus": "Connected"})
    add(conn, "rg-hub-eastus", "Microsoft.Network/bastionHosts", "bas-hub-eastus", location="eastus", tagged=True,
        sku={"name": "Standard"}, props={"ipConfigurations": [{"name": "bas", "properties": {
            "subnet": {"id": f"{hub['eastus']}/subnets/AzureBastionSubnet"}}}]})

    # Ingress, private connectivity and DNS for the production workload
    add(prod, "rg-prd-web", "Microsoft.Network/applicationGateways", "agw-prd-eastus", location="eastus", tagged=True,
        zones=["1", "2", "3"],
        props={"sku": {"name": "WAF_v2", "tier": "WAF_v2"},
               "webApplicationFirewallConfiguration": {"enabled": True, "firewallMode": "Prevention"},
               "gatewayIPConfigurations": [{"name": "agw", "properties": {
                   "subnet": {"id": f"{spoke['prd-eastus']}/subnets/snet-web"}}}]})
    add(prod, "rg-prd-app01", "Microsoft.Network/loadBalancers", "lbi-prd-app", location="eastus", tagged=True,
        sku={"name": "Standard"}, props={"frontendIPConfigurations": [{"name": "fe", "properties": {
            "subnet": {"id": f"{spoke['prd-eastus']}/subnets/snet-app"}}}]})
    for name, target in (("pe-sql-prd-01", _rid(prod, "rg-prd-data", "Microsoft.Sql/servers", "sql-prd-01")),
                         ("pe-stprddata00-blob",
                          _rid(prod, "rg-prd-data", "Microsoft.Storage/storageAccounts", "stprddata00"))):
        add(prod, "rg-prd-network", "Microsoft.Network/privateEndpoints", name, location="eastus", tagged=True,
            props={"subnet": {"id": f"{spoke['prd-eastus']}/subnets/snet-pe"},
                   "privateLinkServiceConnections": [{"name": name, "properties": {
                       "privateLinkServiceId": target}}]})
    for zone in ("privatelink.database.windows.net", "privatelink.blob.core.windows.net"):
        add(conn, "rg-dns", "Microsoft.Network/privateDnsZones", zone, location="global", tagged=True)
