"""Turn a customer-supplied record (a JSON object or a CSV row) into a ``Resource``.

Customers export inventory in many ways, so column names are matched loosely
(case, spaces, underscores and dots are ignored) and nested values may arrive
either as real objects (JSON) or as JSON-encoded strings (CSV). Flattened
columns such as ``properties.diskState`` or ``tags.owner`` are folded back into
their parent object.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..models import Resource

# canonical field -> accepted header spellings (after _key() normalisation)
ALIASES = {
    "id": ["id", "resourceid", "resourceuri"],
    "name": ["name", "resourcename"],
    "type": ["type", "resourcetype"],
    "location": ["location", "region", "azureregion"],
    "resource_group": ["resourcegroup", "resourcegroupname", "rg"],
    "subscription_id": ["subscriptionid"],
    "subscription_name": ["subscriptionname", "subscription", "subscriptiondisplayname"],
    "kind": ["kind"],
    "sku": ["sku", "skuname"],
    "tags": ["tags"],
    "zones": ["zones", "zone", "availabilityzones"],
    "properties": ["properties"],
}
_LOOKUP = {alias: canon for canon, names in ALIASES.items() for alias in names}

# Display names used by the Azure portal "All resources" CSV export -> ARM resource type.
PORTAL_TYPES = {
    "virtual machine": "microsoft.compute/virtualmachines",
    "virtual machine scale set": "microsoft.compute/virtualmachinescalesets",
    "disk": "microsoft.compute/disks",
    "snapshot": "microsoft.compute/snapshots",
    "availability set": "microsoft.compute/availabilitysets",
    "network interface": "microsoft.network/networkinterfaces",
    "virtual network": "microsoft.network/virtualnetworks",
    "network security group": "microsoft.network/networksecuritygroups",
    "public ip address": "microsoft.network/publicipaddresses",
    "load balancer": "microsoft.network/loadbalancers",
    "application gateway": "microsoft.network/applicationgateways",
    "firewall": "microsoft.network/azurefirewalls",
    "route table": "microsoft.network/routetables",
    "private endpoint": "microsoft.network/privateendpoints",
    "storage account": "microsoft.storage/storageaccounts",
    "key vault": "microsoft.keyvault/vaults",
    "sql server": "microsoft.sql/servers",
    "sql database": "microsoft.sql/servers/databases",
    "app service": "microsoft.web/sites",
    "function app": "microsoft.web/sites",
    "app service plan": "microsoft.web/serverfarms",
    "log analytics workspace": "microsoft.operationalinsights/workspaces",
    "kubernetes service": "microsoft.containerservice/managedclusters",
    "container registry": "microsoft.containerregistry/registries",
    "recovery services vault": "microsoft.recoveryservices/vaults",
    "cloud service (classic)": "microsoft.classiccompute/domainnames",
}


def _key(header: str) -> str:
    return re.sub(r"[\s_.\-]", "", header.strip().lower())


def _maybe_json(value: Any) -> Any:
    if isinstance(value, str):
        v = value.strip()
        if v[:1] in "{[" and v[-1:] in "}]":
            try:
                return json.loads(v)
            except ValueError:
                return value
        if v.lower() in ("", "null", "none"):
            return None
        if v.lower() in ("true", "false"):
            return v.lower() == "true"
        if re.fullmatch(r"-?\d+", v):
            return int(v)
    return value


def _parse_tags(value: Any) -> dict[str, str]:
    value = _maybe_json(value)
    if isinstance(value, dict):
        return {str(k): "" if v is None else str(v) for k, v in value.items()}
    if not value or not isinstance(value, str):
        return {}
    # "env:prod; owner:team" or "env=prod, owner=team"
    tags = {}
    for part in re.split(r"[;,]\s*", value):
        if not part.strip():
            continue
        k, _, v = part.partition("=") if "=" in part else part.partition(":")
        tags[k.strip()] = v.strip()
    return tags


def _parse_sku(value: Any) -> dict[str, Any]:
    value = _maybe_json(value)
    if isinstance(value, dict):
        return value
    return {"name": str(value)} if value not in (None, "") else {}


def _parse_zones(value: Any) -> list[str]:
    value = _maybe_json(value)
    if isinstance(value, list):
        return [str(z) for z in value]
    if value in (None, ""):
        return []
    return [z.strip() for z in re.split(r"[;,\s]+", str(value)) if z.strip()]


def _set_path(d: dict, path: list[str], value: Any) -> None:
    for p in path[:-1]:
        d = d.setdefault(p, {})
        if not isinstance(d, dict):
            return
    d[path[-1]] = value


def normalize_type(value: str) -> str:
    t = (value or "").strip().lower()
    return PORTAL_TYPES.get(t, t)


def canonical_fields(headers) -> set[str]:
    """Which canonical fields a set of column/key names supplies (used for data-coverage reporting)."""
    found = set()
    for h in headers:
        head, _, _ = str(h).partition(".")
        canon = _LOOKUP.get(_key(head)) if "." in str(h) else _LOOKUP.get(_key(str(h)))
        if canon:
            found.add(canon)
    return found


def to_resource(raw: dict[str, Any], sub_names: dict[str, str] | None = None) -> Resource:
    fields: dict[str, Any] = {}
    nested: dict[str, dict] = {"properties": {}, "tags": {}, "sku": {}}
    for header, value in raw.items():
        if header is None:
            continue
        header = str(header)
        head, dot, rest = header.partition(".")
        canon = _LOOKUP.get(_key(head)) if dot else _LOOKUP.get(_key(header))
        if dot and canon in nested:
            v = _maybe_json(value)
            if v is not None:
                _set_path(nested[canon], rest.split("."), v)
        elif canon and canon not in fields:
            fields[canon] = value

    props = _maybe_json(fields.get("properties"))
    props = props if isinstance(props, dict) else {}
    props.update(nested["properties"])
    tags = _parse_tags(fields.get("tags"))
    tags.update({k: str(v) for k, v in nested["tags"].items()})
    sku = _parse_sku(fields.get("sku"))
    sku.update(nested["sku"])

    rid = str(fields.get("id") or "").strip()
    rtype = normalize_type(str(fields.get("type") or ""))
    name = str(fields.get("name") or "").strip() or rid.rsplit("/", 1)[-1]
    rg = str(fields.get("resource_group") or "").strip()
    sub_id = str(fields.get("subscription_id") or "").strip()
    if not sub_id and "/subscriptions/" in rid.lower():
        sub_id = rid.split("/")[2]
    if not rg and "/resourcegroups/" in rid.lower():
        parts = rid.split("/")
        rg = parts[[p.lower() for p in parts].index("resourcegroups") + 1]
    sub_name = str(fields.get("subscription_name") or "").strip() or (sub_names or {}).get(sub_id, "")
    sub_id = sub_id or sub_name  # portal exports only carry the subscription display name
    if not rid:
        rid = f"/subscriptions/{sub_id}/resourceGroups/{rg}/providers/{rtype}/{name}"

    return Resource(
        id=rid, name=name, type=rtype,
        location=str(fields.get("location") or "").strip().lower().replace(" ", ""),
        resource_group=rg, subscription_id=sub_id, subscription_name=sub_name or sub_id,
        kind=str(fields.get("kind") or ""), sku=sku, tags=tags,
        zones=_parse_zones(fields.get("zones")), properties=props,
    )
