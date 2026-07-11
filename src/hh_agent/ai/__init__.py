"""ИИ-модуль: скоринг вакансий и сопроводительные письма (Claude / OpenAI-compat)."""

from .fallback import FallbackScorer
from .openai_scorer import OpenAICompatScorer, ProviderUnavailableError
from .scorer import ClaudeScorer, ScoringError

__all__ = [
    "ClaudeScorer",
    "FallbackScorer",
    "OpenAICompatScorer",
    "ProviderUnavailableError",
    "ScoringError",
]
