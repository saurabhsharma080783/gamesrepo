"""AI-drafted report narrative using a self-hosted, open-source language model.

The deterministic engine (rules, scoring, architecture and narrative analysis) stays the
source of truth. The model only rewrites the facts that engine produced into consultant-style
prose; every figure it writes is checked against those facts, and any part that fails the
check keeps the standard wording. See docs/AI-NARRATIVE.md.
"""
from .drafter import AIDraft, draft
from .llm import LLMError, OpenAICompatibleClient

__all__ = ["AIDraft", "draft", "LLMError", "OpenAICompatibleClient"]
