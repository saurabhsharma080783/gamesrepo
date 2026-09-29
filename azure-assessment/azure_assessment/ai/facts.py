"""Fact sheets: the verified, deterministic output the model is allowed to write from.

Each fact sheet is plain text built from the assessment, architecture analysis and
infrastructure narrative. The model sees nothing else (no raw inventory), which keeps
prompts small and makes every statement traceable to the rule engine.

Per-resource table rows are left out on purpose: the reports show those tables anyway, and
in testing a 7B model given the rows mixed up which resource had which setting (a wrong
account listed as LRS, a dev account called production). The summarised statements carry
the counts it needs.
"""
from __future__ import annotations

from ..analysis.architecture import Architecture, ArchDimension
from ..analysis.assessor import Assessment, RuleSummary
from ..analysis.narrative import NarrativeSection

def _related(related: list[RuleSummary]) -> list[str]:
    if not related:
        return ["Related findings: none."]
    out = ["Related findings:"]
    for r in related:
        out.append(f"- {r.rule.id} {r.rule.title} ({r.rule.pillar}, {r.rule.severity} severity): "
                   f"{r.affected} of {r.evaluated} evaluated resources affected. "
                   f"Recommendation: {r.rule.recommendation}")
    return out


def executive(a: Assessment, arch: Architecture) -> str:
    sev = a.severity_counts
    lines = [
        f"Customer: {a.customer}",
        f"Scope: {a.total_resources:,} resources in {len(a.inventory.subscriptions)} subscription(s), "
        f"{a.resource_group_count} resource groups and {len(a.by_location())} region(s), from the inventory "
        f"export supplied by the customer (no direct access to Azure was used).",
        (f"Overall posture score: {a.overall_score}/100, rated {a.overall_rating}"
         + (f" ({a.score_basis})" if a.score_basis else "") + "."
         if a.overall_score is not None else "Overall posture score: not assessed (insufficient data)."),
        "Scoring bands: 80 or more is Good, 60 to 79 is Fair, below 60 is Poor.",
        "Pillar scores:",
    ]
    for p in a.pillar_scores:
        lines.append(f"- {p.pillar}: " + (f"{p.score}/100 ({p.rating})" if p.score is not None else "not assessed")
                     + f", {p.failed} finding(s): {p.high} high, {p.medium} medium, {p.low} low")
    lines.append(f"Findings: {len(a.findings)} in total ({sev['High']} high, {sev['Medium']} medium, "
                 f"{sev['Low']} low severity), affecting {a.affected_resources} resources.")
    if a.tag_coverage is not None:
        lines.append(f"Tag coverage: {a.tag_coverage}% of resources carry all required tags "
                     f"({', '.join(a.config.get('required_tags') or [])}).")
    lines.append("Architecture patterns identified:")
    lines += [f"- {d.title}: {d.pattern} (confidence {d.confidence})" for d in arch.dimensions]
    lines.append("Issues, most severe first:")
    for s in a.top_rules(10):
        lines.append(f"- {s.rule.id} {s.rule.title} ({s.rule.pillar}, {s.rule.severity}): {s.affected} of "
                     f"{s.evaluated} evaluated resources. Recommendation: {s.rule.recommendation}")
    lines.append("Remediation roadmap:")
    for bucket, rules in a.roadmap().items():
        lines.append(f"- {bucket}: " + ("; ".join(r.rule.title for r in rules) if rules else "nothing scheduled"))
    if a.coverage_gaps:
        not_scored = [p.pillar for p in a.pillar_scores if p.score is None]
        lines.append(f"Data limitations: {len(a.coverage_gaps)} check(s) could not be fully assessed because the "
                     "export lacks some data" + (f" ({', '.join(a.missing_fields)})" if a.missing_fields else "")
                     + (f"; not scored: {', '.join(not_scored)}" if not_scored else "") + ".")
    return "\n".join(lines)


def section(ns: NarrativeSection) -> str:
    lines = [f"Section: {ns.title}", "Statements:"]
    lines += [f"- {p}" for p in ns.paragraphs]
    lines += [f"- {b}" for b in ns.bullets]
    lines += _related(ns.related)
    return "\n".join(lines)


def dimension(d: ArchDimension) -> str:
    lines = [f"Area: {d.title}", f"Pattern identified: {d.pattern} (confidence {d.confidence})", "Statements:"]
    lines += [f"- {p}" for p in d.summary]
    if d.evidence:
        lines.append("Evidence from the inventory:")
        lines += [f"- {e}" for e in d.evidence]
    if d.considerations:
        lines.append("Considerations against the Microsoft reference architecture:")
        lines += [f"- {k}" for k in d.considerations]
    lines += _related(d.related)
    return "\n".join(lines)
