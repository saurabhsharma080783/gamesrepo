"""Input loaders: read the inventory files a customer shares (CSV or JSON)."""
from __future__ import annotations

from pathlib import Path

from ..models import Inventory
from . import csv_loader, json_loader

LOADERS = {".csv": csv_loader.load, ".txt": csv_loader.load, ".json": json_loader.load}


def load(paths: list[str | Path] | str | Path) -> Inventory:
    """Load one or more inventory files (e.g. one export per subscription) and merge them."""
    paths = [paths] if isinstance(paths, (str, Path)) else list(paths)
    if not paths:
        raise ValueError("No inventory files given")
    parts = []
    for p in map(Path, paths):
        if not p.is_file():
            raise FileNotFoundError(f"Inventory file not found: {p}")
        loader = LOADERS.get(p.suffix.lower())
        if loader is None:
            raise ValueError(f"{p.name}: unsupported file type (use .csv or .json)")
        parts.append(loader(p))

    seen, resources, subs = set(), [], {}
    for inv in parts:
        subs.update(inv.subscriptions)
        for r in inv.resources:
            key = r.id.lower()
            if key not in seen:  # the same resource exported twice is counted once
                seen.add(key)
                resources.append(r)
    # A field counts as available only if every file supplied it.
    fields = frozenset.intersection(*(inv.available_fields for inv in parts))
    return Inventory(resources=resources, subscriptions=subs,
                     tenant_id=next((i.tenant_id for i in parts if i.tenant_id), ""),
                     collected_at=next((i.collected_at for i in parts if i.collected_at), ""),
                     source=", ".join(i.source for i in parts), available_fields=fields)
