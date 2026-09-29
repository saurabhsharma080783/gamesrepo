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
    return json.loads(Path(path).read_text(encoding="utf-8-sig")) if path else {}


def customer_slug(customer: str) -> str:
    slug = "".join(c if c.isalnum() else "-" for c in customer.strip().lower())
    return "-".join(filter(None, slug.split("-"))) or "azure"


def _ai_draft(a, settings):
    """Draft narrative with the LLM; on any server problem, warn and keep the standard wording."""
    from . import ai
    from .ai.settings import SettingsError

    try:
        llm = settings.client()
        llm.check()
    except (ai.LLMError, SettingsError) as exc:
        print(f"warning: AI narrative skipped, standard wording used: {exc}", file=sys.stderr)
        return None
    print(f"Drafting narrative with {llm.model} at {llm.base_url}"
          + (f" ({settings.hosting} model)" if settings.hosting else "") + " ...")
    d = ai.draft(a, llm, workers=settings.workers, progress=lambda p: print(
        f"  {'AI      ' if p.status == 'ai' else 'standard'} {p.title}" + (f"  ({p.reason})" if p.reason else "")))
    d.hosting, d.provider = settings.hosting, settings.provider
    print(f"AI drafted {d.drafted} of {len(d.parts)} narrative parts; the rest keep the standard wording.")
    print("Review the AI text before the report goes to the customer: edit 'paragraphs' in the "
          "*-ai-draft.json file, then rebuild with --ai-draft <file> --reviewed-by \"<name>\".")
    return d


def _reviewed_draft(a, path: str, reviewed_by: str):
    from datetime import datetime, timezone

    from .ai import AIDraft, apply_review

    d = AIDraft.load(path)
    for w in apply_review(a, d):
        print(f"warning: {w}", file=sys.stderr)
    if reviewed_by:
        d.reviewed_by, d.reviewed_at = reviewed_by, datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"Using {'reviewed' if d.reviewed_by else 'unreviewed'} AI draft {path}: {d.drafted} AI part(s), "
          f"{d.edited} edited by the reviewer.")
    return d


def generate_reports(inventory: Inventory, out_dir: Path, customer: str, formats=DEFAULT_FORMATS,
                     config: dict | None = None, llm_settings=None, ai_draft: str | None = None,
                     reviewed_by: str = "") -> dict[str, Path]:
    """Build the reports. With ``llm_settings`` the narrative is drafted by the model; with ``ai_draft``
    it comes from a saved (usually reviewed) draft file and no model is needed."""
    from .reports import charts, html_report, ppt_report, word_report

    a = assess(inventory, config, customer)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"{customer_slug(customer)}-azure-assessment"
    written: dict[str, Path] = {}
    if ai_draft:
        a.ai_draft = _reviewed_draft(a, ai_draft, reviewed_by)
    elif llm_settings is not None:
        a.ai_draft = _ai_draft(a, llm_settings)
    if a.ai_draft is not None:
        # Kept next to the reports for review, and as facts -> text pairs for later fine-tuning.
        draft_path = out_dir / f"{base}-ai-draft.json"
        draft_path.write_text(json.dumps(a.ai_draft.to_dict(), indent=2), encoding="utf-8")
        written["ai-draft"] = draft_path
    with tempfile.TemporaryDirectory() as tmp:
        imgs = charts.render_all(a, Path(tmp)) if {"docx", "pptx"} & set(formats) else {}
        if "html" in formats:
            written["html"] = html_report.build(a, out_dir / f"{base}.html")
        if "docx" in formats:
            written["docx"] = word_report.build(a, imgs, out_dir / f"{base}.docx")
        if "pptx" in formats:
            written["pptx"] = ppt_report.build(a, imgs, out_dir / f"{base}.pptx")
    findings_path = out_dir / f"{base}-findings.json"
    findings_path.write_text(json.dumps([f.__dict__ for f in a.findings], indent=2), encoding="utf-8")
    written["findings"] = findings_path

    print(f"Assessed {a.total_resources} resources: score {a.overall_score_label}/100, {len(a.findings)} findings")
    if a.coverage_gaps:
        print(f"Note: {len(a.coverage_gaps)} check(s) lacked data in the input"
              + (f" (missing: {', '.join(a.missing_fields)})" if a.missing_fields else "")
              + "; see 'Data coverage' in the reports.")
    return written


def _llm_settings(args):
    from .ai import settings
    return settings.load(args.llm_config, url=args.llm_url, model=args.llm_model, timeout=args.llm_timeout,
                         workers=args.llm_workers)


def _ai_check(args) -> int:
    from .ai import LLMError
    from .ai.settings import SettingsError
    try:
        s = _llm_settings(args)
        client = s.client()
        print(f"Settings: {s.source or '(none; defaults and command line)'}")
        print(f"Hosting:  {s.hosting or 'not stated'}" + (f" ({s.provider})" if s.provider else ""))
        print(f"Server:   {client.base_url}   model: {client.model}   API key: {'set' if client.api_key else 'none'}")
        client.check()
        reply = client.chat("Reply with the single word OK.", "Are you there?").strip()
    except (SettingsError, LLMError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"OK: the model answered ({reply[:40]!r})")
    return 0


def _ai_training(drafts: list[str], out: Path) -> int:
    from .ai import AIDraft, training_examples
    n = skipped = 0
    with out.open("w", encoding="utf-8") as fh:
        for path in drafts:
            try:
                examples = training_examples(AIDraft.load(path))
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            if not examples:
                skipped += 1
                print(f"skipped {path}: not reviewed (rebuild it with --ai-draft and --reviewed-by first)",
                      file=sys.stderr)
            for ex in examples:
                fh.write(json.dumps(ex, ensure_ascii=False) + "\n")
                n += 1
    print(f"Wrote {n} training example(s) from {len(drafts) - skipped} reviewed draft(s) to {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="azure-assess",
                                 description="Build Azure assessment reports from a customer's inventory export")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def llm_opts(g):
        g.add_argument("--llm-config", metavar="FILE", help="LLM settings file (default: $AZURE_ASSESS_LLM_CONFIG, "
                                                            "/etc/azure-assess/llm.json, ~/.config/azure-assess/llm.json)")
        g.add_argument("--llm-url", help="OpenAI-compatible API base URL (overrides the settings file; default "
                                         "http://localhost:11434/v1)")
        g.add_argument("--llm-model", help="Model name (overrides the settings file; default qwen2.5:7b-instruct)")
        g.add_argument("--llm-timeout", type=float, help="Seconds per request (default: 900)")
        g.add_argument("--llm-workers", type=int, help="Parallel requests (default: 1; raise only for a GPU server)")

    def report_opts(p):
        p.add_argument("-o", "--out", help="Output directory (default: the project's reports/ folder)")
        p.add_argument("-c", "--customer", default="Customer", help="Customer name shown on the reports")
        p.add_argument("-f", "--formats", default=",".join(DEFAULT_FORMATS),
                       help="Comma list of docx,html,pptx (default: docx,html)")
        p.add_argument("--config", help="JSON file overriding assessment settings (see examples/config.json)")
        p.add_argument("--inventory-date", help="Date the customer exported the inventory (shown on the reports)")
        g = p.add_argument_group("AI narrative (self-hosted open-source LLM, see docs/AI-NARRATIVE.md)")
        g.add_argument("--ai", action="store_true",
                       help="Draft the executive summary and section narrative with a language model")
        g.add_argument("--ai-draft", metavar="FILE",
                       help="Build from a saved (reviewed) *-ai-draft.json instead of calling the model")
        g.add_argument("--reviewed-by", metavar="NAME", default="",
                       help="With --ai-draft: record who reviewed the AI text (shown in the reports)")
        llm_opts(g)

    p = sub.add_parser("report", help="Build reports from customer inventory file(s)")
    p.add_argument("inputs", nargs="+", metavar="FILE", help="Inventory export(s): .csv or .json; several are merged")
    report_opts(p)

    p = sub.add_parser("demo", help="Build reports from a built-in sample inventory")
    report_opts(p)

    p = sub.add_parser("template", help="Write a sample inventory file showing the expected input format")
    p.add_argument("-o", "--out", default="inventory-template.csv", help="Output path ending in .csv or .json")

    p = sub.add_parser("ai-check", help="Check the LLM settings and that the model server answers")
    llm_opts(p)

    p = sub.add_parser("ai-training", help="Export reviewed AI drafts as fine-tuning examples (JSONL)")
    p.add_argument("drafts", nargs="+", metavar="DRAFT", help="Reviewed *-ai-draft.json files")
    p.add_argument("-o", "--out", default="ai-training.jsonl", help="Output .jsonl file")

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")

    if args.cmd == "ai-check":
        return _ai_check(args)
    if args.cmd == "ai-training":
        return _ai_training(args.drafts, Path(args.out))

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
    settings = None
    if args.ai and args.ai_draft:
        ap.error("use either --ai (draft with the model) or --ai-draft (use a saved draft), not both")
    if args.reviewed_by and not args.ai_draft:
        ap.error("--reviewed-by goes with --ai-draft")
    if args.ai:
        from .ai.settings import SettingsError
        try:
            settings = _llm_settings(args)
            settings.validate()   # a misconfiguration stops the run; an unreachable server only warns
        except SettingsError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    try:
        written = generate_reports(inv, out_dir, args.customer, formats, _load_config(args.config),
                                   llm_settings=settings, ai_draft=args.ai_draft, reviewed_by=args.reviewed_by)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for kind, path in written.items():
        print(f"  {kind:8s} {path}")
    return 0
