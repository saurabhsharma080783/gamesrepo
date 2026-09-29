"""Grounding checks applied to every model draft before it can reach a report."""
from __future__ import annotations

import re

RULE_ID = re.compile(r"\b[A-Z]{2,5}-\d{3}\b")
NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")
_MD = re.compile(r"^\s*(?:#{1,6}\s+|[-*•]\s+|\d+[.)]\s+)")
_PREAMBLE = re.compile(r"^(here is|here's|sure|certainly|below is)\b", re.I)


def _is_heading(para: str) -> bool:
    """'Significant Risks and Their Business Impact': short, no sentence punctuation."""
    return len(para.split()) <= 8 and not para.rstrip().endswith((".", "!", "?", ":", ";"))


def _norm(n: str) -> str:
    n = n.replace(",", "").rstrip(".")
    if "." in n:
        n = n.rstrip("0").rstrip(".")
    return n.lstrip("0") or "0"


def numbers(text: str) -> set[str]:
    return {_norm(m) for m in NUMBER.findall(RULE_ID.sub(" ", text))}


def clean(text: str) -> list[str]:
    """Normalise model output into paragraphs, dropping markdown and chatty preambles."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)       # reasoning models
    text = text.replace("**", "").replace("__", "").replace("`", "")
    paras = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [_MD.sub("", ln).strip() for ln in block.splitlines()]
        para = " ".join(ln for ln in lines if ln)
        if para and not (len(lines) == 1 and _is_heading(para)) and not (len(paras) == 0 and _PREAMBLE.match(para)
                                                   and para.endswith(":")):
            paras.append(para)
    return paras


def check(paragraphs: list[str], facts: str, min_words: int = 25) -> str | None:
    """Return a description of the problem, or None when the draft is grounded in the facts."""
    text = " ".join(paragraphs)
    unknown_ids = set(RULE_ID.findall(text)) - set(RULE_ID.findall(facts))
    if unknown_ids:
        return "it cites rule IDs that are not in the facts: " + ", ".join(sorted(unknown_ids))
    unknown = numbers(text) - numbers(facts)
    if unknown:
        return "it contains figures that are not in the facts: " + ", ".join(sorted(unknown, key=len))
    if len(text.split()) < min_words:
        return "the draft is empty or too short"
    return None
