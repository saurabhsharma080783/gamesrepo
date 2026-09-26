"""Load an inventory from JSON so reports can be built offline.

Accepted shapes:
  * this tool's own export (``{"resources": [...], "subscriptions": [...]}``)
  * ``az graph query -q "Resources | ..." -o json`` output (``{"data": [...]}``)
  * ``az resource list -o json`` output (a bare list)
"""
from __future__ import annotations

import json
from pathlib import Path

from ..models import Inventory, Resource


def load(path: str | Path) -> Inventory:
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(raw, list):
        records, meta = raw, {}
    elif isinstance(raw, dict):
        records = raw.get("resources") or raw.get("data") or raw.get("value") or []
        meta = raw
    else:
        raise ValueError(f"Unrecognised inventory format in {path}")

    sub_names: dict[str, str] = {}
    for s in meta.get("subscriptions", []) or []:
        sid = s.get("id") or s.get("subscriptionId")
        sub_names[sid] = s.get("name") or s.get("displayName") or sid

    resources = [Resource.from_dict(r, sub_names) for r in records]
    for r in resources:
        sub_names.setdefault(r.subscription_id, r.subscription_name or r.subscription_id)

    inv = Inventory(resources=resources, subscriptions=sub_names, tenant_id=meta.get("tenant_id", ""),
                    source=meta.get("source", f"file:{Path(path).name}"))
    if meta.get("collected_at"):
        inv.collected_at = meta["collected_at"]
    return inv


def save(inventory: Inventory, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(inventory.to_dict(), indent=2), encoding="utf-8")
    return path
