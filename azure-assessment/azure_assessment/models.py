"""Core data models shared by collectors, analysis and report generators."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

SEVERITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}
SEVERITY_WEIGHT = {"High": 5, "Medium": 3, "Low": 1}
PILLARS = ["Security", "Reliability", "Cost Optimization", "Operational Excellence", "Governance"]


@dataclass
class Resource:
    id: str
    name: str
    type: str
    location: str = ""
    resource_group: str = ""
    subscription_id: str = ""
    subscription_name: str = ""
    kind: str = ""
    sku: dict[str, Any] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)
    zones: list[str] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], sub_names: dict[str, str] | None = None) -> "Resource":
        """Normalise a record from Resource Graph, `az` CLI output or our own export."""
        sub_id = raw.get("subscription_id") or raw.get("subscriptionId") or ""
        rid = raw.get("id", "")
        if not sub_id and "/subscriptions/" in rid.lower():
            sub_id = rid.split("/")[2]
        sub_name = raw.get("subscription_name") or raw.get("subscriptionName") or (sub_names or {}).get(sub_id, "")
        return cls(
            id=rid,
            name=raw.get("name", ""),
            type=(raw.get("type") or "").lower(),
            location=(raw.get("location") or "").lower(),
            resource_group=raw.get("resource_group") or raw.get("resourceGroup") or "",
            subscription_id=sub_id,
            subscription_name=sub_name or sub_id,
            kind=raw.get("kind") or "",
            sku=raw.get("sku") or {},
            tags=raw.get("tags") or {},
            zones=raw.get("zones") or [],
            properties=raw.get("properties") or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Finding:
    rule_id: str
    title: str
    pillar: str
    severity: str
    resource_id: str
    resource_name: str
    resource_type: str
    resource_group: str
    subscription: str
    detail: str
    recommendation: str


@dataclass
class Inventory:
    resources: list[Resource]
    subscriptions: dict[str, str] = field(default_factory=dict)  # id -> display name
    tenant_id: str = ""
    collected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    source: str = "azure-resource-graph"

    def to_dict(self) -> dict[str, Any]:
        return {
            "collected_at": self.collected_at,
            "tenant_id": self.tenant_id,
            "source": self.source,
            "subscriptions": [{"id": k, "name": v} for k, v in self.subscriptions.items()],
            "resources": [r.to_dict() for r in self.resources],
        }
