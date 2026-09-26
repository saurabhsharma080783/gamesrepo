"""Command-line interface: ``azure-assess collect | report | run | demo``."""
from __future__ import annotations

import argparse
import json
import logging
import tempfile
from pathlib import Path

from .analysis.assessor import assess
from .collectors import file_collector
from .models import Inventory

FORMATS = ("html", "docx", "pptx")


def _load_config(path: str | None) -> dict:
    return json.loads(Path(path).read_text()) if path else {}


def generate_reports(inventory: Inventory, out_dir: Path, customer: str, formats=FORMATS,
                     config: dict | None = None) -> dict[str, Path]:
    from .reports import charts, html_report, ppt_report, word_report

    a = assess(inventory, config, customer)
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = "".join(c if c.isalnum() else "-" for c in customer).strip("-").lower() or "azure"
    base = f"{slug}-azure-assessment"
    written: dict[str, Path] = {}
    with tempfile.TemporaryDirectory() as tmp:
        imgs = charts.render_all(a, Path(tmp)) if {"docx", "pptx"} & set(formats) else {}
        if "html" in formats:
            written["html"] = html_report.build(a, out_dir / f"{base}.html")
        if "docx" in formats:
            written["docx"] = word_report.build(a, imgs, out_dir / f"{base}.docx")
        if "pptx" in formats:
            written["pptx"] = ppt_report.build(a, imgs, out_dir / f"{base}.pptx")
    findings_path = out_dir / f"{base}-findings.json"
    findings_path.write_text(json.dumps([f.__dict__ for f in a.findings], indent=2))
    written["findings"] = findings_path
    print(f"Assessed {a.total_resources} resources: score {a.overall_score}/100, {len(a.findings)} findings")
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="azure-assess", description="Azure inventory assessment report generator")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def scope(p):
        p.add_argument("-s", "--subscription", action="append", default=[], help="Subscription ID (repeatable)")
        p.add_argument("-m", "--management-group", action="append", default=[], help="Management group (repeatable)")

    def report_opts(p):
        p.add_argument("-o", "--out", default="reports", help="Output directory")
        p.add_argument("-c", "--customer", default="Contoso", help="Customer/organisation name on the report")
        p.add_argument("-f", "--formats", default=",".join(FORMATS), help="Comma list of html,docx,pptx")
        p.add_argument("--config", help="JSON file overriding assessment settings (see examples/config.json)")

    p = sub.add_parser("collect", help="Collect inventory from Azure to a JSON file")
    scope(p)
    p.add_argument("-o", "--out", default="inventory.json")

    p = sub.add_parser("report", help="Build reports from a saved inventory JSON")
    p.add_argument("-i", "--input", required=True, help="Inventory JSON (own export, az graph query or az resource list)")
    report_opts(p)

    p = sub.add_parser("run", help="Collect from Azure and build reports in one step")
    scope(p)
    report_opts(p)

    p = sub.add_parser("demo", help="Build reports from a built-in sample inventory")
    report_opts(p)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")

    if args.cmd == "collect":
        from .collectors import azure_collector
        inv = azure_collector.collect(args.subscription, args.management_group)
        print(f"Saved {len(inv.resources)} resources to {file_collector.save(inv, args.out)}")
        return 0

    if args.cmd == "report":
        inv = file_collector.load(args.input)
    elif args.cmd == "run":
        from .collectors import azure_collector
        inv = azure_collector.collect(args.subscription, args.management_group)
        file_collector.save(inv, Path(args.out) / "inventory.json")
    else:
        from .collectors import sample
        inv = sample.build()

    formats = [f.strip().lower() for f in args.formats.split(",") if f.strip()]
    bad = set(formats) - set(FORMATS)
    if bad:
        ap.error(f"unknown format(s): {', '.join(sorted(bad))}")
    written = generate_reports(inv, Path(args.out), args.customer, formats, _load_config(args.config))
    for kind, path in written.items():
        print(f"  {kind:8s} {path}")
    return 0
