"""Self-contained interactive HTML report (no external assets)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from ..analysis import architecture, narrative
from ..analysis.assessor import Assessment, friendly_type
from ..models import PILLARS
from . import diagram
from .theme import SEVERITY_COLORS, rating_color


def build(a: Assessment, out: Path) -> Path:
    env = Environment(loader=PackageLoader("azure_assessment", "reports/templates"),
                      autoescape=select_autoescape(["html", "j2"]))
    env.filters["rating_color"] = rating_color
    tpl = env.get_template("report.html.j2")

    def bars(items):
        top = max((n for _, n in items), default=1) or 1
        return [{"label": k, "value": n, "pct": round(100 * n / top, 1)} for k, n in items]

    arch = architecture.analyse(a)
    sections = narrative.build(a)
    described_in = {}  # rule id -> narrative section, for "context" links from each finding
    for i, ns in enumerate(sections, 1):
        for r in ns.related:
            described_in.setdefault(r.rule.id, (f"env-{ns.key}", f"{i}. {ns.title}"))

    html = tpl.render(
        sections=sections, described_in=described_in, arch=arch, topology_svg=diagram.render_svg(arch.topology),
        a=a, ai=a.ai_draft, pillars=PILLARS, sev_colors=SEVERITY_COLORS,
        generated=datetime.now().strftime("%d %B %Y %H:%M"),
        by_type=bars(a.by_type(12)), by_region=bars(a.by_location(12)), by_sub=bars(a.by_subscription(12)),
        resources=sorted(a.inventory.resources, key=lambda r: (r.subscription_name, r.resource_group.lower(), r.name)),
        friendly_type=friendly_type,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
