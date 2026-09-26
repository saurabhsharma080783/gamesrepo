"""Live collection from Azure using Azure Resource Graph.

Authentication uses ``DefaultAzureCredential``, so any of these work:
``az login``, environment variables for a service principal
(AZURE_CLIENT_ID / AZURE_TENANT_ID / AZURE_CLIENT_SECRET), managed identity,
or VS Code / Azure PowerShell sign-in. The identity needs **Reader** on the
subscriptions (or management group) being assessed.
"""
from __future__ import annotations

import logging

from ..models import Inventory, Resource

log = logging.getLogger(__name__)

RESOURCES_QUERY = """
Resources
| project id, name, type, location, resourceGroup, subscriptionId, tenantId,
          kind, sku, tags, zones, properties
"""

SUBSCRIPTIONS_QUERY = """
ResourceContainers
| where type =~ 'microsoft.resources/subscriptions'
| project subscriptionId, name, tenantId
"""

PAGE_SIZE = 1000


def _import_sdk():
    try:
        from azure.identity import DefaultAzureCredential
        from azure.mgmt.resourcegraph import ResourceGraphClient
        from azure.mgmt.resourcegraph.models import QueryRequest, QueryRequestOptions
    except ImportError as exc:  # pragma: no cover - depends on optional extras
        raise SystemExit(
            "Azure SDK packages are not installed. Run: pip install 'azure-assessment[azure]'"
        ) from exc
    return DefaultAzureCredential, ResourceGraphClient, QueryRequest, QueryRequestOptions


def _run_query(client, QueryRequest, QueryRequestOptions, query, subscriptions, management_groups):
    rows: list[dict] = []
    skip_token = None
    while True:
        options = QueryRequestOptions(top=PAGE_SIZE, skip_token=skip_token, result_format="objectArray")
        request = QueryRequest(
            query=query,
            subscriptions=subscriptions or None,
            management_groups=management_groups or None,
            options=options,
        )
        response = client.resources(request)
        rows.extend(response.data)
        skip_token = response.skip_token
        log.debug("Fetched %d rows (total %d)", len(response.data), len(rows))
        if not skip_token:
            return rows


def collect(subscriptions: list[str] | None = None, management_groups: list[str] | None = None) -> Inventory:
    """Collect every ARM resource visible to the signed-in identity.

    With no scope given, Resource Graph queries all subscriptions the identity can read.
    """
    DefaultAzureCredential, ResourceGraphClient, QueryRequest, QueryRequestOptions = _import_sdk()
    client = ResourceGraphClient(DefaultAzureCredential())

    subs = _run_query(client, QueryRequest, QueryRequestOptions, SUBSCRIPTIONS_QUERY, subscriptions, management_groups)
    sub_names = {s["subscriptionId"]: s["name"] for s in subs}
    tenant_id = subs[0].get("tenantId", "") if subs else ""

    rows = _run_query(client, QueryRequest, QueryRequestOptions, RESOURCES_QUERY, subscriptions, management_groups)
    log.info("Collected %d resources across %d subscriptions", len(rows), len(sub_names))
    resources = [Resource.from_dict(r, sub_names) for r in rows]
    return Inventory(resources=resources, subscriptions=sub_names, tenant_id=tenant_id)
