"""Microsoft Word (.docx) assessment report."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from ..analysis import architecture, narrative
from ..analysis.assessor import Assessment, friendly_type
from ..models import PILLARS
from . import diagram
from .theme import ACCENT, INK_2, PRIMARY, SEVERITY_COLORS, hex_to_rgb, rating_color

MAX_APPENDIX_ROWS = 500


def _sev_text(sev):
    """Severity colour for text on white; the chart yellow is too light to read, so Low uses dark amber."""
    return "#9A6700" if sev == "Low" else SEVERITY_COLORS[sev]


def _rgb(h):
    return RGBColor(*hex_to_rgb(h))


def _shade(cell, hex_color):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color.lstrip("#"))
    tc_pr.append(shd)


def _repeat_header(row):
    tr_pr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    tr_pr.append(el)


def _fit_columns(doc, t, weights):
    """Fix column widths to fill the text width. Weights are relative, e.g. [1, 4, 1].

    Widths go in the table grid and on every cell, which is what both Word and LibreOffice honour.
    """
    sec = doc.sections[-1]
    avail = sec.page_width - sec.left_margin - sec.right_margin
    total = sum(weights)
    widths = [int(avail * w / total) for w in weights]
    t.autofit = False
    tbl_pr = t._tbl.tblPr
    for el in tbl_pr.findall(qn("w:tblW")):
        tbl_pr.remove(el)
    tbl_w = OxmlElement("w:tblW")
    tbl_w.set(qn("w:w"), str(int(avail / 635)))  # EMU -> twentieths of a point
    tbl_w.set(qn("w:type"), "dxa")
    tbl_pr.append(tbl_w)
    for gc, w in zip(t._tbl.tblGrid.findall(qn("w:gridCol")), widths):
        gc.set(qn("w:w"), str(int(w / 635)))
    for row in t.rows:
        for cell, w in zip(row.cells, widths):
            cell.width = w


def _table(doc, headers, rows, widths=None, font_size=9):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = t.rows[0]
    _repeat_header(hdr)
    for i, h in enumerate(headers):
        c = hdr.cells[i]
        c.text = ""
        run = c.paragraphs[0].add_run(h)
        run.bold = True
        run.font.size = Pt(font_size)
        run.font.color.rgb = RGBColor(255, 255, 255)
        _shade(c, PRIMARY)
    for ri, row in enumerate(rows):
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = ""
            run = cells[i].paragraphs[0].add_run(str(v))
            run.font.size = Pt(font_size)
            if headers[i] == "Severity" and v in SEVERITY_COLORS:
                run.bold = True
                run.font.color.rgb = _rgb(_sev_text(v))
            if ri % 2:
                _shade(cells[i], "F2F5F9")
    _fit_columns(doc, t, widths or [1] * len(headers))
    doc.add_paragraph()
    return t


def _toc(doc):
    """Insert a Word TOC field; Word fills it in when the user presses F9 / opens with update."""
    p = doc.add_paragraph()
    run = p.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = 'TOC \\o "1-2" \\h \\z \\u'
    fld_sep = OxmlElement("w:fldChar")
    fld_sep.set(qn("w:fldCharType"), "separate")
    txt = OxmlElement("w:t")
    txt.text = "Right-click and choose 'Update Field' to build the table of contents."
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    for el in (fld_begin, instr, fld_sep, txt, fld_end):
        run._r.append(el)
    # Ask Word to refresh fields (the TOC) when the document is opened.
    upd = OxmlElement("w:updateFields")
    upd.set(qn("w:val"), "true")
    doc.settings.element.append(upd)


def _setup_styles(doc):
    st = doc.styles["Normal"]
    st.font.name = "Calibri"
    st.font.size = Pt(10.5)
    for name, size in (("Heading 1", 18), ("Heading 2", 14), ("Heading 3", 12)):
        s = doc.styles[name]
        s.font.name = "Calibri"
        s.font.size = Pt(size)
        s.font.color.rgb = _rgb(PRIMARY)


def _footer(section, text):
    p = section.footer.paragraphs[0]
    p.text = text
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for r in p.runs:
        r.font.size = Pt(8)
        r.font.color.rgb = _rgb(INK_2)


def build(a: Assessment, charts: dict[str, Path], out: Path) -> Path:
    doc = Document()
    _setup_styles(doc)
    sec = doc.sections[0]
    sec.left_margin = sec.right_margin = Cm(2.2)
    _footer(sec, f"{a.customer} · Azure Environment Assessment · Confidential")

    # ---- Cover -------------------------------------------------------------
    for _ in range(6):
        doc.add_paragraph()
    p = doc.add_paragraph()
    r = p.add_run("AZURE ENVIRONMENT ASSESSMENT")
    r.font.size, r.bold, r.font.color.rgb = Pt(12), True, _rgb(ACCENT)
    p = doc.add_paragraph()
    r = p.add_run(a.customer)
    r.font.size, r.bold, r.font.color.rgb = Pt(34), True, _rgb(PRIMARY)
    p = doc.add_paragraph()
    r = p.add_run("Inventory, Well-Architected findings and remediation roadmap")
    r.font.size, r.font.color.rgb = Pt(14), _rgb(INK_2)
    doc.add_paragraph()
    meta = [("Report date", datetime.now().strftime("%d %B %Y")),
            ("Inventory export date", a.inventory.collected_at),
            ("Inventory source", a.inventory.source),
            ("Subscriptions in scope", str(len(a.inventory.subscriptions))),
            ("Resources assessed", f"{a.total_resources:,}")]
    meta = [(k, v) for k, v in meta if v]
    for k, v in meta:
        p = doc.add_paragraph()
        r1 = p.add_run(f"{k}:  ")
        r1.bold = True
        p.add_run(v)
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    doc.add_heading("Contents", level=1)
    _toc(doc)
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    # ---- 1. Executive summary ---------------------------------------------
    doc.add_heading("1. Executive summary", level=1)
    p = doc.add_paragraph()
    p.add_run("Overall posture score: ").bold = True
    r = p.add_run(f"{a.overall_score}/100 ({a.overall_rating})" if a.overall_score is not None
                  else "Not assessed (insufficient data in the inventory export)")
    r.bold, r.font.size, r.font.color.rgb = True, Pt(14), _rgb(rating_color(a.overall_score))
    if a.score_basis and a.overall_score is not None:
        r = p.add_run(f"   {a.score_basis}; see Data coverage and limitations.")
        r.italic, r.font.color.rgb = True, _rgb(INK_2)

    sev = a.severity_counts
    kpis = [("Resources", f"{a.total_resources:,}"), ("Subscriptions", len(a.inventory.subscriptions)),
            ("Resource groups", a.resource_group_count), ("Regions", len(a.by_location())),
            ("High findings", sev["High"]), ("Medium findings", sev["Medium"]),
            ("Low findings", sev["Low"]), ("Tag coverage", a.tag_coverage_label)]
    t = doc.add_table(rows=2, cols=4)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, (label, value) in enumerate(kpis):
        cell = t.rows[i // 4].cells[i % 4]
        _shade(cell, "EEF3F9")
        cell.text = ""
        pv = cell.paragraphs[0]
        pv.alignment = WD_ALIGN_PARAGRAPH.CENTER
        rv = pv.add_run(str(value))
        rv.bold, rv.font.size, rv.font.color.rgb = True, Pt(18), _rgb(PRIMARY)
        pl = cell.add_paragraph()
        pl.alignment = WD_ALIGN_PARAGRAPH.CENTER
        rl = pl.add_run(label)
        rl.font.size, rl.font.color.rgb = Pt(8.5), _rgb(INK_2)
    doc.add_paragraph()

    arch = architecture.analyse(a)
    doc.add_heading("Key observations", level=2)
    for o in a.key_observations()[:1] + [arch.headline] + a.key_observations()[1:]:
        doc.add_paragraph(o, style="List Bullet")

    doc.add_heading("Scorecard", level=2)
    _table(doc, ["Pillar", "Score", "Rating", "Checks", "Findings", "High", "Medium", "Low"],
           [(p.pillar, p.score_label, p.rating, p.checks, p.failed, p.high, p.medium, p.low) for p in a.pillar_scores],
           widths=[3.9, 1.3, 1.8, 1.5, 1.7, 1.2, 1.8, 1.1])
    doc.add_picture(str(charts["pillars"]), width=Cm(15))

    # ---- 2. Scope & methodology -------------------------------------------
    doc.add_heading("2. Scope and methodology", level=1)
    doc.add_paragraph(
        f"The assessment is based on the resource inventory export supplied by {a.customer} "
        f"({a.inventory.source or 'inventory file'}). No access to the Azure environment was required. "
        "Each resource was evaluated against a rule set aligned to the "
        "Microsoft Azure Well-Architected Framework pillars (Security, Reliability, Cost Optimization, "
        "Operational Excellence) plus a Governance pillar covering tagging, regions and deployment model.")
    doc.add_paragraph(
        "Pillar scores are the severity-weighted pass rate of all rule evaluations in that pillar "
        "(High = 5, Medium = 3, Low = 1). 80+ is rated Good, 60–79 Fair, and below 60 Poor.")
    _table(doc, ["Subscription", "Subscription ID", "Resources"],
           [(name, sid, sum(1 for r in a.inventory.resources if r.subscription_id == sid))
            for sid, name in a.inventory.subscriptions.items()], widths=[5, 8, 2.2])

    if a.coverage_gaps:
        doc.add_heading("Data coverage and limitations", level=2)
        doc.add_paragraph(
            "Some checks could not be evaluated because the inventory export does not contain the data they need"
            + (f" (missing: {', '.join(a.missing_fields)})" if a.missing_fields else "")
            + ". These checks are excluded from the scores rather than treated as passed. For a complete "
              "assessment, provide an Azure Resource Graph export that includes the tags, sku, zones and "
              "properties columns.")
        _table(doc, ["Rule", "Check", "Pillar", "Data needed", "Not assessed"],
               [(g.rule.id, g.rule.title, g.rule.pillar, g.missing, f"{g.skipped} of {g.applicable}")
                for g in a.coverage_gaps], widths=[1.8, 6.5, 3, 2.5, 2.5])

    finding_ref = narrative.pillar_section_numbers("6")

    def related_findings(related):
        if not related:
            return
        hp = doc.add_paragraph()
        hp.add_run("Related findings").bold = True
        hp.paragraph_format.keep_with_next = True
        for r in related:
            rp = doc.add_paragraph(style="List Bullet")
            rp.add_run(f"{r.rule.id} {r.rule.title}: ")
            rs = rp.add_run(r.rule.severity)
            rs.bold, rs.font.color.rgb = True, _rgb(_sev_text(r.rule.severity))
            rp.add_run(f", {narrative.plural(r.affected, 'resource')} (see {finding_ref[r.rule.pillar]} "
                       f"{r.rule.pillar})")

    def label(text):
        lp = doc.add_paragraph()
        lr = lp.add_run(text)
        lr.bold, lr.font.color.rgb = True, _rgb(PRIMARY)
        lp.paragraph_format.keep_with_next = True

    # ---- 3. Architecture assessment -------------------------------------
    doc.add_heading("3. Architecture assessment", level=1)
    doc.add_paragraph(
        "This section identifies the architecture patterns the environment follows, such as its network "
        "topology, hybrid connectivity and subscription model, and compares them with Microsoft's reference "
        "architectures (the Cloud Adoption Framework Azure landing zone and the Well-Architected Framework). "
        "Each area states the pattern identified, the evidence for it in the inventory, and considerations.")
    doc.add_paragraph(
        "Confidence shows how firmly the inventory supports each conclusion: High means direct configuration "
        "evidence, Medium means inferred from resource types and naming, Low means the export lacked the data. "
        "Identity (Microsoft Entra ID), role assignments, Azure Policy and management groups are not part of a "
        "resource inventory and are outside the scope of this section.")
    label("Architecture at a glance")
    _table(doc, ["Area", "Pattern identified", "Confidence"],
           [(f"3.{i} {d.title}", d.pattern, d.confidence) for i, d in enumerate(arch.dimensions, 1)],
           widths=[4.2, 8.9, 2.3])
    for i, d in enumerate(arch.dimensions, 1):
        doc.add_heading(f"3.{i} {d.title}", level=2)
        pp = doc.add_paragraph()
        pp.add_run("Pattern identified: ").bold = True
        pr = pp.add_run(d.pattern)
        pr.bold, pr.font.color.rgb = True, _rgb(ACCENT)
        cr = pp.add_run(f"   (confidence: {d.confidence})")
        cr.italic, cr.font.color.rgb = True, _rgb(INK_2)
        for para in d.summary:
            doc.add_paragraph(para)
        if d.key == "network":
            png = diagram.render_png(arch.topology, Path(charts["pillars"]).parent / "topology.png")
            if png:
                doc.add_picture(str(png), width=Cm(16.5))
                cap = doc.add_paragraph()
                cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
                crun = cap.add_run(f"Figure: network topology derived from VNet peerings ({arch.topology.pattern}).")
                crun.italic, crun.font.size, crun.font.color.rgb = True, Pt(9), _rgb(INK_2)
        if d.evidence:
            label("Evidence from the inventory")
            for e in d.evidence:
                doc.add_paragraph(e, style="List Bullet")
        for caption, headers, rows, widths in d.tables:
            label(caption)
            _table(doc, headers, rows, widths=widths, font_size=8 if len(headers) >= 6 else 8.5)
        if d.considerations:
            label("Considerations against the reference architecture")
            for k in d.considerations:
                doc.add_paragraph(k, style="List Bullet")
        related_findings(d.related)

    # ---- 4. Infrastructure overview (narrative) ----------------------------
    sections = narrative.build(a)
    described_in: dict[str, str] = {}  # rule id -> "4.x Title", for back-references from section 6
    doc.add_heading("4. Infrastructure overview", level=1)
    doc.add_paragraph(
        "This section describes the environment as understood from the inventory export: what it contains, "
        "how it is organised and how each area is configured. Each part ends with the findings that relate to "
        "it, so the description can be read alongside the findings in section 6 and the roadmap in section 7.")
    for i, ns in enumerate(sections, 1):
        num = f"4.{i}"
        doc.add_heading(f"{num} {ns.title}", level=2)
        for para in ns.paragraphs:
            doc.add_paragraph(para)
        for b in ns.bullets:
            name, _, rest = b.partition(": ")
            bp = doc.add_paragraph(style="List Bullet")
            bp.add_run(f"{name}: ").bold = True
            bp.add_run(rest)
        for caption, t in ns.tables:
            cp = doc.add_paragraph()
            cr = cp.add_run(caption)
            cr.bold, cr.font.size, cr.font.color.rgb = True, Pt(9.5), _rgb(INK_2)
            cp.paragraph_format.keep_with_next = True
            _table(doc, t.headers, t.rows, widths=t.widths)
        if ns.related:
            related_findings(ns.related)
            for r in ns.related:
                described_in.setdefault(r.rule.id, f"{num} {ns.title}")

    # ---- 5. Inventory ------------------------------------------------------
    doc.add_heading("5. Inventory charts and statistics", level=1)
    doc.add_picture(str(charts["by_type"]), width=Cm(15))
    doc.add_picture(str(charts["by_region"]), width=Cm(15))
    if len(a.inventory.subscriptions) > 1:
        doc.add_picture(str(charts["by_subscription"]), width=Cm(15))
    doc.add_heading("Resource types", level=2)
    _table(doc, ["Resource type", "Count", "% of estate"],
           [(t, n, f"{100 * n / max(a.total_resources, 1):.1f}%") for t, n in a.by_type()], widths=[6, 2, 2])

    # ---- 5. Findings by pillar --------------------------------------------
    doc.add_heading("6. Assessment findings", level=1)
    doc.add_picture(str(charts["pillar_findings"]), width=Cm(15))
    for i, pillar in enumerate(PILLARS, 1):
        ps = next(p for p in a.pillar_scores if p.pillar == pillar)
        doc.add_heading(f"6.{i} {pillar} — " + (f"{ps.score}/100 ({ps.rating})" if ps.score is not None
                                                  else "Not assessed"), level=2)
        rules = a.rules_for(pillar)
        if not rules:
            doc.add_paragraph("The inventory export did not contain the data needed to assess this pillar."
                              if ps.score is None else "No issues were identified in this pillar.")
            continue
        for s in rules:
            doc.add_heading(f"{s.rule.id}: {s.rule.title}", level=3)
            p = doc.add_paragraph()
            p.add_run("Severity: ").bold = True
            rs = p.add_run(s.rule.severity)
            rs.bold, rs.font.color.rgb = True, _rgb(_sev_text(s.rule.severity))
            p.add_run(f"    Affected: {s.affected} of {s.evaluated} evaluated resources")
            p = doc.add_paragraph()
            p.add_run("Recommendation: ").bold = True
            p.add_run(s.rule.recommendation)
            p = doc.add_paragraph()
            p.add_run("Examples: ").bold = True
            p.add_run(", ".join(s.examples) + (" …" if s.affected > len(s.examples) else ""))
            if s.rule.id in described_in:
                p = doc.add_paragraph()
                r = p.add_run(f"Context: see {described_in[s.rule.id]} in the infrastructure overview.")
                r.italic, r.font.color.rgb = True, _rgb(INK_2)

    # ---- 6. Roadmap --------------------------------------------------------
    doc.add_heading("7. Remediation roadmap", level=1)
    for phase, items in a.roadmap().items():
        doc.add_heading(phase, level=2)
        if not items:
            doc.add_paragraph("No actions in this phase.")
            continue
        _table(doc, ["ID", "Action", "Severity", "Pillar", "Resources"],
               [(s.rule.id, s.rule.recommendation, s.rule.severity, s.rule.pillar, s.affected) for s in items],
               widths=[1.6, 8.6, 1.7, 3.2, 1.9])

    # ---- Appendices (landscape) -------------------------------------------
    new = doc.add_section()
    new.orientation = WD_ORIENT.LANDSCAPE
    new.page_width, new.page_height = sec.page_height, sec.page_width
    new.left_margin = new.right_margin = Cm(1.5)
    doc.add_heading("Appendix A. Detailed findings", level=1)
    rows = [(f.rule_id, f.severity, f.resource_name, f.resource_type, f.resource_group, f.subscription, f.detail)
            for f in a.findings[:MAX_APPENDIX_ROWS]]
    _table(doc, ["Rule", "Severity", "Resource", "Type", "Resource group", "Subscription", "Detail"], rows,
           widths=[1.6, 1.6, 3.2, 3, 2.8, 3.4, 6.5], font_size=8)
    if len(a.findings) > MAX_APPENDIX_ROWS:
        doc.add_paragraph(f"Showing first {MAX_APPENDIX_ROWS} of {len(a.findings)} findings; "
                          "see the HTML report for the full list.")

    doc.add_heading("Appendix B. Resource inventory", level=1)
    res = sorted(a.inventory.resources, key=lambda r: (r.subscription_name, r.resource_group.lower(), r.type, r.name))
    _table(doc, ["Name", "Type", "Resource group", "Location", "Subscription", "SKU"],
           [(r.name, friendly_type(r.type), r.resource_group, r.location, r.subscription_name,
             r.sku.get("name", "") if isinstance(r.sku, dict) else "") for r in res[:MAX_APPENDIX_ROWS]],
           widths=[4, 3.2, 3, 2, 3.6, 2.4], font_size=8)
    if len(res) > MAX_APPENDIX_ROWS:
        doc.add_paragraph(f"Showing first {MAX_APPENDIX_ROWS} of {len(res)} resources; "
                          "see the HTML report or JSON export for the full inventory.")

    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    return out
