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

    return Inventory(resources=resources, subscriptions=dict(SUBS),
                     tenant_id="00000000-0000-0000-0000-000000000000", source="sample")
