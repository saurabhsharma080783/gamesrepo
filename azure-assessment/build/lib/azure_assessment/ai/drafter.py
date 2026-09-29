"""Draft report narrative with a self-hosted LLM, grounded in the assessment's facts.

The draft is saved as ``<customer>-azure-assessment-ai-draft.json``. A consultant reviews it by editing
each part's ``paragraphs`` (the model's own text stays in ``original``) and rebuilds the reports with
``azure-assess report ... --ai-draft <file> --reviewed-by "<name>"``. Reviewed drafts double as
fine-tuning data (``azure-assess ai-training``).
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..analysis import architecture, narrative
from ..analysis.assessor import Assessment
from . import facts as fact_sheets
from . import guard, prompts
from .llm import ChatClient, LLMError

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class DraftPart:
    kind: str          # executive / section / architecture
    key: str
    title: str
    facts: str
    paragraphs: list[str] = field(default_factory=list)   # text used in the reports; reviewers edit this
    status: str = "standard"                               # ai / standard
    reason: str = ""                                       # why the standard wording was kept
    attempts: int = 0
    original: list[str] = field(default_factory=list)     # the model's accepted text, before review

    @property
    def edited(self) -> bool:
        return self.status == "ai" and self.paragraphs != self.original


@dataclass
class AIDraft:
    model: str
    parts: list[DraftPart]
    generated_at: str = field(default_factory=_now)
    hosting: str = ""        # local / hosted / "" (see settings.py)
    provider: str = ""       # hosted mode: who operates the model server
    customer: str = ""
    reviewed_by: str = ""
    reviewed_at: str = ""

    def _get(self, kind: str, key: str) -> list[str]:
        return next((p.paragraphs for p in self.parts if p.kind == kind and p.key == key and p.status == "ai"), [])

    def executive(self) -> list[str]:
        return self._get("executive", "summary")

    def section(self, key: str) -> list[str]:
        return self._get("section", key)

    def architecture(self, key: str) -> list[str]:
        return self._get("architecture", key)

    @property
    def drafted(self) -> int:
        return sum(p.status == "ai" for p in self.parts)

    @property
    def edited(self) -> int:
        return sum(p.edited for p in self.parts)

    @property
    def note(self) -> str:
        where = {
            "local": (f"The model ran on a virtual machine inside {self.customer or 'the customer'}'s own "
                      "environment, so no assessment data left that environment. "),
            "hosted": (f"The model ran on a server operated by {self.provider or 'the assessor'}. Only the "
                       "summarised facts described here were sent to it, over an encrypted connection; the "
                       "inventory itself was not. "),
        }.get(self.hosting, "")
        review = (f"The AI-drafted passages were reviewed by {self.reviewed_by} on {self.reviewed_at[:10]}"
                  + (f", who corrected {self.edited} of them. " if self.edited else ". ")
                  if self.reviewed_by else
                  "The AI-drafted passages have not yet been reviewed by a consultant. ")
        return (f"Narrative marked as AI-drafted was written by the open-source language model {self.model} "
                "from the verified facts produced by the assessment engine; the model was given those facts only, "
                f"not the inventory itself. {where}Every figure and rule ID in the drafted text was checked "
                "against those facts, and any passage that failed the check uses the standard wording. "
                f"{review}Scores, findings and tables are not AI-generated.")

    def to_dict(self) -> dict:
        head = {k: getattr(self, k) for k in ("model", "generated_at", "hosting", "provider", "customer",
                                              "reviewed_by", "reviewed_at")}
        return {**head, "drafted": self.drafted, "edited": self.edited, "total": len(self.parts),
                "parts": [asdict(p) for p in self.parts]}

    @classmethod
    def from_dict(cls, data: dict) -> "AIDraft":
        part_keys = {f.name for f in fields(DraftPart)}
        parts = [DraftPart(**{k: v for k, v in p.items() if k in part_keys}) for p in data.get("parts", [])]
        for p in parts:
            if p.status == "ai" and not p.original:   # drafts written before 'original' existed
                p.original = list(p.paragraphs)
        head = {k: data.get(k, "") for k in ("generated_at", "hosting", "provider", "customer",
                                               "reviewed_by", "reviewed_at")}
        return cls(model=data.get("model", ""), parts=parts, **head)

    @classmethod
    def load(cls, path: str | Path) -> "AIDraft":
        try:
            return cls.from_dict(json.loads(Path(path).read_text()))
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"cannot read AI draft {path}: {exc}") from exc


def _parts(a: Assessment) -> list[DraftPart]:
    """Every part the model drafts, with its fact sheet built from the current assessment."""
    arch = architecture.analyse(a)
    parts = [DraftPart("executive", "summary", "Executive summary", fact_sheets.executive(a, arch))]
    parts += [DraftPart("architecture", d.key, d.title, fact_sheets.dimension(d)) for d in arch.dimensions]
    parts += [DraftPart("section", ns.key, ns.title, fact_sheets.section(ns)) for ns in narrative.build(a)]
    return parts


def prompt_for(part: DraftPart) -> str:
    if part.kind == "executive":
        return prompts.EXECUTIVE.format(facts=part.facts)
    tpl = prompts.ARCHITECTURE if part.kind == "architecture" else prompts.SECTION
    return tpl.format(title=part.title, facts=part.facts)


def _run(client: ChatClient, system: str, prompt: str, part: DraftPart, retries: int) -> DraftPart:
    user = prompt
    for attempt in range(1, retries + 2):
        part.attempts = attempt
        try:
            paras = guard.clean(client.chat(system, user))
        except LLMError as exc:
            part.reason = str(exc)
            log.warning("AI draft of %s '%s' failed: %s", part.kind, part.title, exc)
            return part
        problem = guard.check(paras, part.facts)
        if problem is None:
            part.paragraphs, part.original, part.status, part.reason = paras, list(paras), "ai", ""
            return part
        part.reason = problem
        log.info("AI draft of %s '%s' rejected (attempt %d): %s", part.kind, part.title, attempt, problem)
        user = prompt + "\n\n" + prompts.RETRY.format(problem=problem, draft="\n\n".join(paras))
    return part


def draft(a: Assessment, client: ChatClient, *, workers: int = 2, retries: int = 1,
          progress: Callable[[DraftPart], None] | None = None) -> AIDraft:
    """Draft the executive summary, architecture summaries and infrastructure overview paragraphs.

    Parts run concurrently (``workers``); a CPU-only server should use 1.
    """
    system = prompts.SYSTEM.format(customer=a.customer)

    def work(part):
        part = _run(client, system, prompt_for(part), part, retries)
        if progress:
            progress(part)
        return part

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        parts = list(pool.map(work, _parts(a)))
    return AIDraft(client.model, parts, customer=a.customer)


def apply_review(a: Assessment, d: AIDraft) -> list[str]:
    """Check a (reviewed) draft against the current assessment before its text is used.

    A part whose facts no longer match (the inventory or configuration changed since drafting) is set
    back to the standard wording. Edited text is re-checked for figures and rule IDs not in the facts;
    that is reported as a warning only, because a person wrote it. Returns the warnings.
    """
    warnings = []
    current = {(p.kind, p.key): p.facts for p in _parts(a)}
    if d.customer and d.customer != a.customer:
        warnings.append(f"the draft was written for '{d.customer}', not '{a.customer}'")
    for p in d.parts:
        if p.status != "ai":
            continue
        facts = current.get((p.kind, p.key))
        if facts is None or facts != p.facts:
            p.status, p.reason = "standard", "facts changed since the draft was written"
            warnings.append(f"{p.title}: the facts have changed since drafting, so the standard wording is used")
            continue
        p.paragraphs = [para.strip() for para in p.paragraphs if para.strip()]
        if not p.paragraphs:
            p.status, p.reason = "standard", "removed by the reviewer"
        elif p.edited and (problem := guard.check(p.paragraphs, p.facts, min_words=1)):
            warnings.append(f"{p.title}: check the reviewed text; {problem}")
    return warnings


def training_examples(d: AIDraft) -> list[dict]:
    """Reviewed parts as chat examples (system, facts prompt, approved text) for fine-tuning."""
    if not d.reviewed_by:
        return []
    system = prompts.SYSTEM.format(customer=d.customer or "the customer")
    return [{"messages": [{"role": "system", "content": system},
                          {"role": "user", "content": prompt_for(p)},
                          {"role": "assistant", "content": "\n\n".join(p.paragraphs)}],
             "metadata": {"part": f"{p.kind}/{p.key}", "edited": p.edited, "model": d.model,
                          "reviewed_by": d.reviewed_by}}
            for p in d.parts if p.status == "ai"]
