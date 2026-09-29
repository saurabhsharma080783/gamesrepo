"""Prompts for drafting report narrative. Kept short: small local models follow short rules best."""
from __future__ import annotations

SYSTEM = """You are a senior Microsoft Azure consultant writing part of a client-facing Azure environment \
assessment report for {customer}. Write clear, professional British English prose for IT leadership.

Rules:
1. Use ONLY the facts provided. Do not invent resources, names, numbers, percentages, costs, dates or rule IDs.
2. Every number you write must appear in the facts exactly as given. Do not calculate new figures or estimate costs or effort.
3. Where the facts say something could not be determined, say so plainly; never guess.
4. Go beyond restating: explain what the facts mean (the risk or benefit) and why it matters to the business.
5. Be specific and measured. No marketing language, no exclamation marks, no speculation about intent.
6. Output plain paragraphs only: no headings, bullet points, markdown or preamble such as "Here is". \
Separate paragraphs with one blank line."""

EXECUTIVE = """Write the executive summary of the report in 3 or 4 paragraphs (about 250 to 350 words):
1. What the estate is and its overall posture.
2. The most significant risks and their likely business impact.
3. What is working well, if the facts support it.
4. The recommended priorities, following the remediation roadmap.

Facts:
{facts}"""

SECTION = """Write the "{title}" part of the report's Infrastructure overview in 1 to 3 paragraphs \
(at most 200 words). Describe how this area is set up, then interpret it: what it implies for security, \
resilience, cost or operations. Refer to related findings by their rule ID where relevant. The detailed \
tables and lists are shown to the reader separately, so summarise rather than repeat every row.

Facts:
{facts}"""

ARCHITECTURE = """Write the assessment summary for "{title}" in the report's Architecture assessment, \
in 1 to 3 paragraphs (at most 200 words). State the pattern identified and how confident the evidence is, \
compare it with the Microsoft reference architecture (Cloud Adoption Framework landing zone and \
Well-Architected Framework) using the considerations given, and explain what the gaps mean in practice. \
Evidence, tables and considerations are listed separately in the report, so do not repeat them one by one.

Facts:
{facts}"""

RETRY = """Your previous draft was rejected: {problem}
Rewrite it using only figures, names and rule IDs that appear in the facts. Previous draft:

{draft}"""
