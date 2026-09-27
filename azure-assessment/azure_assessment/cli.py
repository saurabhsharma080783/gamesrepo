"""Command-line interface: ``azure-assess report | demo | template``.

The tool never connects to Azure: the customer exports their inventory (CSV or JSON)
and this CLI turns it into Word, PowerPoint and HTML reports.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from pathlib import Path

from . import loaders
from .analysis.assessor import assess
from .loaders import csv_loader, json_loader, sample
from .models import Inventory

FORMATS = ("html", "docx", "pptx")
# Reports are written to the project's reports/ folder, which is committed to the repository.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = _PROJECT_ROOT / "reports" if (_PROJECT_ROOT / "pyproject.toml").exists() else Path("reports")
DEFAULT_FORMATS = ("docx", "html")  # PowerPoint on request: -f docx,html,pptx


def _load_config(path: str | None) -> dict:
    return json.loads(Path(path).read_text()) if path else {}


def customer_slug(customer: str) -> str:
    slug = "".join(c if c.isalnum() else "-" for c in customer.strip().lower())
    return "-".join(filter(None, slug.split("-"))) or "azure"


def generate_reports(inventory: Inventory, out_dir: Path, customer: str, formats=DEFAULT_FORMATS,
                     config: dict | None = None) -> dict[str, Path]:
    from .reports import charts, html_report, ppt_report, word_report

    a = assess(inventory, config, customer)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"{customer_slug(customer)}-azure-assessment"
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

    print(f"Assessed {a.total_resources} resources: score {a.overall_score_label}/100, {len(a.findings)} findings")
    if a.coverage_gaps:
        print(f"Note: {len(a.coverage_gaps)} check(s) lacked data in the input"
              + (f" (missing: {', '.join(a.missing_fields)})" if a.missing_fields else "")
              + "; see 'Data coverage' in the reports.")
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="azure-assess",
                                 description="Build Azure assessment reports from a customer's inventory export")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def report_opts(p):
        p.add_argument("-o", "--out", help="Output directory (default: the project's reports/ folder)")
        p.add_argument("-c", "--customer", default="Customer", help="Customer name shown on the reports")
        p.add_argument("-f", "--formats", default=",".join(DEFAULT_FORMATS),
                       help="Comma list of docx,html,pptx (default: docx,html)")
        p.add_argument("--config", help="JSON file overriding assessment settings (see examples/config.json)")
        p.add_argument("--inventory-date", help="Date the customer exported the inventory (shown on the reports)")

    p = sub.add_parser("report", help="Build reports from customer inventory file(s)")
    p.add_argument("inputs", nargs="+", metavar="FILE", help="Inventory export(s): .csv or .json; several are merged")
    report_opts(p)

    p = sub.add_parser("demo", help="Build reports from a built-in sample inventory")
    report_opts(p)

    p = sub.add_parser("template", help="Write a sample inventory file showing the expected input format")
    p.add_argument("-o", "--out", default="inventory-template.csv", help="Output path ending in .csv or .json")

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")

    if args.cmd == "template":
        inv = sample.build()
        out = Path(args.out)
        if out.suffix.lower() == ".json":
            json_loader.save(inv, out)
        elif out.suffix.lower() == ".csv":
            csv_loader.save(inv.resources, out)
        else:
            ap.error("template output must end in .csv or .json")
        print(f"Wrote {len(inv.resources)} sample resources to {out}")
        return 0

    formats = [f.strip().lower() for f in args.formats.split(",") if f.strip()]
    bad = set(formats) - set(FORMATS)
    if bad:
        ap.error(f"unknown format(s): {', '.join(sorted(bad))}")

    if args.cmd == "report":
        try:
            inv = loaders.load(args.inputs)
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if not inv.resources:
            print("error: the inventory file(s) contain no resources", file=sys.stderr)
            return 2
    else:
        inv = sample.build()
    if args.inventory_date:
        inv.collected_at = args.inventory_date

    out_dir = Path(args.out) if args.out else REPORTS_DIR
    written = generate_reports(inv, out_dir, args.customer, formats, _load_config(args.config))
    for kind, path in written.items():
        print(f"  {kind:8s} {path}")
    return 0
