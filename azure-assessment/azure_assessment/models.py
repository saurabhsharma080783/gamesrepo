"""Core data models shared by input loaders, analysis and report generators."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
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


# Optional per-resource fields an input file may or may not supply. Rules that need a field
# the input lacks are reported as "not assessed" instead of silently passing.
OPTIONAL_FIELDS = frozenset({"tags", "sku", "kind", "zones", "properties"})


@dataclass
class Inventory:
    resources: list[Resource]
    subscriptions: dict[str, str] = field(default_factory=dict)  # id -> display name
    tenant_id: str = ""
    collected_at: str = ""  # when the customer exported the inventory, if known
    source: str = ""
    available_fields: frozenset[str] = OPTIONAL_FIELDS

    def to_dict(self) -> dict[str, Any]:
        return {
            "collected_at": self.collected_at,
            "tenant_id": self.tenant_id,
            "source": self.source,
            "subscriptions": [{"id": k, "name": v} for k, v in self.subscriptions.items()],
            "resources": [r.to_dict() for r in self.resources],
        }
