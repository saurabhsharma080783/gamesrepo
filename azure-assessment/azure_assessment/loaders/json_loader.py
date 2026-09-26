"""Load a customer inventory from JSON.

Accepted shapes:
  * ``az graph query -q "Resources | ..." -o json`` output (``{"data": [...]}``)
  * ``az resource list -o json`` output, or any bare list of resource objects
  * ARM REST responses (``{"value": [...]}``)
  * this tool's own template/export (``{"resources": [...], "subscriptions": [...]}``)
"""
from __future__ import annotations

import json
from pathlib import Path

from ..models import Inventory
from .normalize import canonical_fields, to_resource


def load(path: str | Path) -> Inventory:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} is not valid JSON: {exc}") from exc
    if isinstance(raw, list):
        records, meta = raw, {}
    elif isinstance(raw, dict):
        records = raw.get("resources") or raw.get("data") or raw.get("value") or []
        meta = raw
    else:
        raise ValueError(f"Unrecognised inventory format in {path.name}")
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise ValueError(f"{path.name}: expected a list of resource objects")

    sub_names: dict[str, str] = {}
    for s in meta.get("subscriptions", []) or []:
        sid = s.get("id") or s.get("subscriptionId")
        sub_names[sid] = s.get("name") or s.get("displayName") or sid

    resources = [to_resource(r, sub_names) for r in records]
    fields = canonical_fields({k for r in records for k in r})
    return Inventory(resources=resources, subscriptions=sub_names, tenant_id=meta.get("tenant_id", ""),
                     collected_at=meta.get("collected_at", ""), source=path.name,
                     available_fields=frozenset(fields))


def save(inventory: Inventory, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(inventory.to_dict(), indent=2), encoding="utf-8")
    return path
