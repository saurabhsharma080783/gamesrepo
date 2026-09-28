"""Load a customer inventory from CSV.

Works with:
  * Resource Graph Explorer "Download as CSV" (nested columns such as ``properties``
    and ``tags`` arrive as JSON strings)
  * Azure portal "All resources" → "Export to CSV" (NAME, TYPE, RESOURCE GROUP, LOCATION,
    SUBSCRIPTION; friendly type names are mapped back to ARM types)
  * hand-built sheets, including flattened columns like ``properties.diskState`` or ``tags.owner``

Comma, semicolon and tab delimiters are detected automatically (Excel in many
European locales saves with semicolons).
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from ..models import Inventory, Resource
from .normalize import canonical_fields, to_resource

TEMPLATE_COLUMNS = ["id", "name", "type", "location", "resourceGroup", "subscriptionId", "subscriptionName",
                    "kind", "sku", "tags", "zones", "properties"]


def load(path: str | Path) -> Inventory:
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(text.splitlines(), dialect=dialect)
    headers = reader.fieldnames or []
    fields = canonical_fields(headers)
    if not {"name", "id"} & fields or not {"type", "id"} & fields:
        raise ValueError(f"{path.name}: CSV needs at least resource name and type columns "
                         f"(found: {', '.join(headers) or 'none'})")
    rows = [row for row in reader if any((v or "").strip() for v in row.values() if isinstance(v, str))]
    resources = [to_resource(r) for r in rows]
    subs = {r.subscription_id: r.subscription_name for r in resources}
    return Inventory(resources=resources, subscriptions=subs, source=path.name,
                     available_fields=frozenset(fields))


def _cell(v):
    return json.dumps(v) if isinstance(v, (dict, list)) else ("" if v is None else v)


def save(resources: list[Resource], path: str | Path) -> Path:
    """Write resources in the template CSV layout (nested fields as JSON strings)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(TEMPLATE_COLUMNS)
        for r in resources:
            w.writerow([_cell(v) for v in (r.id, r.name, r.type, r.location, r.resource_group, r.subscription_id,
                                           r.subscription_name, r.kind, r.sku, r.tags, r.zones, r.properties)])
    return path
