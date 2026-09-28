"""Run rules against an inventory and aggregate everything the reports need."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ..models import PILLARS, SEVERITY_ORDER, SEVERITY_WEIGHT, Finding, Inventory
from .rules import RULES, Rule

DEFAULT_CONFIG = {
    "required_tags": ["environment", "owner", "costCenter"],
    "allowed_locations": [],
    "min_log_retention_days": 90,
    "disabled_rules": [],
}

FRIENDLY_TYPES = {
    "microsoft.compute/virtualmachines": "Virtual machines",
    "microsoft.compute/disks": "Managed disks",
    "microsoft.compute/virtualmachinescalesets": "VM scale sets",
    "microsoft.network/networkinterfaces": "Network interfaces",
    "microsoft.network/virtualnetworks": "Virtual networks",
    "microsoft.network/networksecuritygroups": "Network security groups",
    "microsoft.network/publicipaddresses": "Public IP addresses",
    "microsoft.network/azurefirewalls": "Azure Firewalls",
    "microsoft.network/loadbalancers": "Load balancers",
    "microsoft.network/applicationgateways": "Application gateways",
    "microsoft.storage/storageaccounts": "Storage accounts",
    "microsoft.keyvault/vaults": "Key vaults",
    "microsoft.sql/servers": "SQL servers",
    "microsoft.sql/servers/databases": "SQL databases",
    "microsoft.web/sites": "App Services",
    "microsoft.web/serverfarms": "App Service plans",
    "microsoft.operationalinsights/workspaces": "Log Analytics workspaces",
    "microsoft.containerservice/managedclusters": "AKS clusters",
    "microsoft.classiccompute/domainnames": "Classic cloud services",
}


def friendly_type(t: str) -> str:
    return FRIENDLY_TYPES.get(t, t.split("/", 1)[-1] if "/" in t else t)


@dataclass
class PillarScore:
    pillar: str
    score: int | None   # 0-100 weighted pass rate; None when the input had no data to assess this pillar
    checks: int         # rule evaluations
    failed: int
    high: int = 0
    medium: int = 0
    low: int = 0

    @property
    def rating(self) -> str:
        return rating_for(self.score)

    @property
    def score_label(self) -> str:
        return "n/a" if self.score is None else str(self.score)


def rating_for(score: int | None) -> str:
    if score is None:
        return "Not assessed"
    return "Good" if score >= 80 else "Fair" if score >= 60 else "Poor"


@dataclass
class CoverageGap:
    """A rule that could not be (fully) evaluated because the input file lacked the data it needs."""
    rule: Rule
    applicable: int   # resources of a type the rule covers
    skipped: int      # of those, how many had no data for the rule

    @property
    def missing(self) -> str:
        return ", ".join(self.rule.needs)


@dataclass
class RuleSummary:
    rule: Rule
    affected: int
    evaluated: int
    examples: list[str] = field(default_factory=list)


@dataclass
class Assessment:
    inventory: Inventory
    findings: list[Finding]
    pillar_scores: list[PillarScore]
    rule_summaries: list[RuleSummary]
    config: dict
    customer: str = "Customer"
    coverage_gaps: list[CoverageGap] = field(default_factory=list)

    # --- convenience aggregates ------------------------------------------
    @property
    def total_resources(self) -> int:
        return len(self.inventory.resources)

    @property
    def overall_score(self) -> int:
        scored = [p for p in self.pillar_scores if p.score is not None]
        return round(sum(p.score for p in scored) / len(scored)) if scored else None

    @property
    def overall_rating(self) -> str:
        return rating_for(self.overall_score)

    @property
    def score_basis(self) -> str:
        """Caveat shown next to the overall score when some pillars could not be scored."""
        n = sum(1 for p in self.pillar_scores if p.score is not None)
        return "" if n == len(self.pillar_scores) else f"Based on {n} of {len(self.pillar_scores)} pillars"

    @property
    def overall_score_label(self) -> str:
        return "n/a" if self.overall_score is None else str(self.overall_score)

    def count_by(self, attr: str, top: int | None = None) -> list[tuple[str, int]]:
        c = Counter(getattr(r, attr) or "(none)" for r in self.inventory.resources)
        return c.most_common(top)

    def by_type(self, top: int | None = None) -> list[tuple[str, int]]:
        return [(friendly_type(t), n) for t, n in self.count_by("type", top)]

    def by_location(self, top=None):
        return self.count_by("location", top)

    def by_subscription(self, top=None):
        return self.count_by("subscription_name", top)

    @property
    def resource_group_count(self) -> int:
        return len({(r.subscription_id, r.resource_group.lower()) for r in self.inventory.resources})

    @property
    def severity_counts(self) -> dict[str, int]:
        c = Counter(f.severity for f in self.findings)
        return {s: c.get(s, 0) for s in SEVERITY_ORDER}

    @property
    def tag_coverage(self) -> int | None:
        req = [t.lower() for t in self.config.get("required_tags") or []]
        res = self.inventory.resources
        if "tags" not in self.inventory.available_fields or not req or not res:
            return None
        ok = sum(1 for r in res if set(req) <= {k.lower() for k in r.tags})
        return round(100 * ok / len(res))

    @property
    def tag_coverage_label(self) -> str:
        return "n/a" if self.tag_coverage is None else f"{self.tag_coverage}%"

    @property
    def unassessed_rules(self) -> list[CoverageGap]:
        return [g for g in self.coverage_gaps if g.skipped == g.applicable]

    @property
    def missing_fields(self) -> list[str]:
        return sorted({f for g in self.coverage_gaps for f in g.rule.needs
                       if f not in self.inventory.available_fields})

    @property
    def affected_resources(self) -> int:
        return len({f.resource_id for f in self.findings})

    def top_rules(self, n: int = 10) -> list[RuleSummary]:
        return self.rule_summaries[:n]

    def findings_for(self, pillar: str) -> list[Finding]:
        return [f for f in self.findings if f.pillar == pillar]

    def rules_for(self, pillar: str) -> list[RuleSummary]:
        return [s for s in self.rule_summaries if s.rule.pillar == pillar]

    def key_observations(self) -> list[str]:
        obs = [f"{self.total_resources:,} resources were assessed across {len(self.inventory.subscriptions)} "
               f"subscription(s), {self.resource_group_count} resource groups and "
               f"{len(self.by_location())} region(s)."]
        sev = self.severity_counts
        obs.append(f"{len(self.findings)} finding(s) were raised: {sev['High']} high, {sev['Medium']} medium "
                   f"and {sev['Low']} low severity, affecting {self.affected_resources} resources.")
        ranked = sorted((p for p in self.pillar_scores if p.score is not None), key=lambda p: p.score)
        if len(ranked) > 1:
            w, b = ranked[0], ranked[-1]
            obs.append(f"{w.pillar} is the weakest area (score {w.score}/100); "
                       f"{b.pillar} is the strongest ({b.score}/100).")
        if self.tag_coverage is not None:
            obs.append(f"{self.tag_coverage}% of resources carry all required tags "
                       f"({', '.join(self.config.get('required_tags') or [])}).")
        highs = [s for s in self.rule_summaries if s.rule.severity == "High"]
        if highs:
            obs.append("Priority high-severity issues: " +
                       "; ".join(f"{s.rule.title} ({s.affected})" for s in highs[:3]) + ".")
        if self.coverage_gaps:
            not_scored = [p.pillar for p in self.pillar_scores if p.score is None]
            msg = (f"{len(self.coverage_gaps)} check(s) could not be fully assessed because the inventory export "
                   f"lacks some data" + (f" ({', '.join(self.missing_fields)})" if self.missing_fields else "") + ".")
            if not_scored:
                msg += f" Not scored: {', '.join(not_scored)}."
            obs.append(msg)
        return obs

    def roadmap(self) -> dict[str, list[RuleSummary]]:
        """Bucket rules into a 30/60/90-day remediation plan by severity."""
        plan = {"0-30 days (quick wins & critical risk)": [], "30-60 days": [], "60-90 days": []}
        keys = list(plan)
        for s in self.rule_summaries:
            if s.rule.severity == "High" or s.rule.pillar == "Cost Optimization":
                plan[keys[0]].append(s)
            elif s.rule.severity == "Medium":
                plan[keys[1]].append(s)
            else:
                plan[keys[2]].append(s)
        return plan


def assess(inventory: Inventory, config: dict | None = None, customer: str = "Customer") -> Assessment:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    disabled = set(cfg.get("disabled_rules") or [])
    rules = [r for r in RULES if r.id not in disabled]

    findings: list[Finding] = []
    evaluated: Counter[str] = Counter()
    per_pillar_weight = defaultdict(lambda: [0, 0, 0])  # [evaluated weight, failed weight, checks]

    # Rules that depend on empty config can never fail; skip them so they don't inflate scores.
    if not cfg.get("allowed_locations"):
        rules = [r for r in rules if r.id != "GOV-002"]
    if not cfg.get("required_tags"):
        rules = [r for r in rules if r.id != "GOV-001"]

    available = inventory.available_fields
    applicable: Counter[str] = Counter()
    skipped: Counter[str] = Counter()

    for res in inventory.resources:
        for rule in rules:
            if not rule.applies_to(res):
                continue
            applicable[rule.id] += 1
            if not _has_data(rule, res, available):
                skipped[rule.id] += 1
                continue
            detail = rule.check(res, cfg)
            w = SEVERITY_WEIGHT[rule.severity]
            evaluated[rule.id] += 1
            pw = per_pillar_weight[rule.pillar]
            pw[0] += w
            pw[2] += 1
            if detail:
                pw[1] += w
                findings.append(Finding(
                    rule_id=rule.id, title=rule.title, pillar=rule.pillar, severity=rule.severity,
                    resource_id=res.id, resource_name=res.name, resource_type=friendly_type(res.type),
                    resource_group=res.resource_group, subscription=res.subscription_name,
                    detail=detail, recommendation=rule.recommendation))

    findings.sort(key=lambda f: (SEVERITY_ORDER[f.severity], f.pillar, f.rule_id, f.resource_name))

    by_rule: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        by_rule[f.rule_id].append(f)
    rule_summaries = [RuleSummary(r, len(by_rule[r.id]), evaluated[r.id], [f.resource_name for f in by_rule[r.id][:5]])
                      for r in rules if by_rule.get(r.id)]
    rule_summaries.sort(key=lambda s: (SEVERITY_ORDER[s.rule.severity], -s.affected))

    pillar_scores = []
    for p in PILLARS:
        ev, failed_w, checks = per_pillar_weight.get(p, [0, 0, 0])
        pf = [f for f in findings if f.pillar == p]
        sc = Counter(f.severity for f in pf)
        pillar_scores.append(PillarScore(
            pillar=p, score=round(100 * (1 - failed_w / ev)) if ev else None, checks=checks, failed=len(pf),
            high=sc["High"], medium=sc["Medium"], low=sc["Low"]))

    gaps = [CoverageGap(r, applicable[r.id], skipped[r.id]) for r in rules if skipped[r.id]]
    return Assessment(inventory, findings, pillar_scores, rule_summaries, cfg, customer, gaps)


def _has_data(rule: Rule, res, available: frozenset[str]) -> bool:
    for f in rule.needs:
        if f not in available:
            return False
        # Per-resource: a blank properties/sku cell means that row carries no data for the check.
        if f in ("properties", "sku") and not getattr(res, f):
            return False
    return True
