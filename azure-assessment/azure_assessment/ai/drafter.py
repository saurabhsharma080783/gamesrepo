"""Draft report narrative with a self-hosted LLM, grounded in the assessment's facts."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Callable

from ..analysis import architecture, narrative
from ..analysis.assessor import Assessment
from . import facts as fact_sheets
from . import guard, prompts
from .llm import ChatClient, LLMError

log = logging.getLogger(__name__)


@dataclass
class DraftPart:
    kind: str          # executive / section / architecture
    key: str
    title: str
    facts: str
    paragraphs: list[str] = field(default_factory=list)   # empty when the standard wording is kept
    status: str = "standard"                               # ai / standard
    reason: str = ""                                       # why the standard wording was kept
    attempts: int = 0


@dataclass
class AIDraft:
    model: str
    parts: list[DraftPart]
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

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
    def note(self) -> str:
        return (f"Narrative marked as AI-drafted was written by the open-source language model {self.model} "
                "from the verified facts produced by the assessment engine; the model was given those facts only, "
                "not the inventory itself. Every figure and rule ID in the "
                "drafted text was checked against those facts, and any passage that failed the check uses the "
                "standard wording. Scores, findings and tables are not AI-generated.")

    def to_dict(self) -> dict:
        return {"model": self.model, "generated_at": self.generated_at, "drafted": self.drafted,
                "total": len(self.parts), "parts": [asdict(p) for p in self.parts]}


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
            part.paragraphs, part.status, part.reason = paras, "ai", ""
            return part
        part.reason = problem
        log.info("AI draft of %s '%s' rejected (attempt %d): %s", part.kind, part.title, attempt, problem)
        user = prompt + "\n\n" + prompts.RETRY.format(problem=problem, draft="\n\n".join(paras))
    return part


def draft(a: Assessment, client: ChatClient, *, workers: int = 2, retries: int = 1,
          progress: Callable[[DraftPart], None] | None = None) -> AIDraft:
    """Draft the executive summary, architecture summaries and infrastructure overview paragraphs.

    Parts run concurrently (``workers``); a local server with one GPU usually serves 1-4 at a time.
    """
    arch = architecture.analyse(a)
    system = prompts.SYSTEM.format(customer=a.customer)
    jobs: list[tuple[DraftPart, str]] = []

    ex = DraftPart("executive", "summary", "Executive summary", fact_sheets.executive(a, arch))
    jobs.append((ex, prompts.EXECUTIVE.format(facts=ex.facts)))
    for d in arch.dimensions:
        p = DraftPart("architecture", d.key, d.title, fact_sheets.dimension(d))
        jobs.append((p, prompts.ARCHITECTURE.format(title=d.title, facts=p.facts)))
    for ns in narrative.build(a):
        p = DraftPart("section", ns.key, ns.title, fact_sheets.section(ns))
        jobs.append((p, prompts.SECTION.format(title=ns.title, facts=p.facts)))

    def work(job):
        part = _run(client, system, job[1], job[0], retries)
        if progress:
            progress(part)
        return part

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        parts = list(pool.map(work, jobs))
    return AIDraft(client.model, parts)
