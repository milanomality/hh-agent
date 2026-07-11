"""Failover-цепочка бесплатных LLM-провайдеров.

Провайдер, ответивший 429/5xx или недоступный по сети
(ProviderUnavailableError), уходит в cooldown и пропускается, пока не остынет;
прочие ScoringError переключают на следующего без cooldown. Все недоступны —
ScoringError: поллер не пометит вакансию seen и вернётся к ней следующим проходом.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from ..models import Resume, ScoreResult, Vacancy
from .openai_scorer import OpenAICompatScorer, ProviderUnavailableError
from .scorer import ScoringError

logger = logging.getLogger(__name__)

T = TypeVar("T")


class FallbackScorer:
    """Реализация ScorerProto: перебирает провайдеров в порядке списка."""

    def __init__(
        self,
        scorers: list[OpenAICompatScorer],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not scorers:
            raise ValueError("Нужен хотя бы один LLM-провайдер, список пуст.")
        self._scorers = list(scorers)
        self._clock = clock  # monotonic; инжектируется в тестах
        self._cooldown_until: dict[int, float] = {}  # индекс провайдера → дедлайн
        self._last_error: dict[int, str] = {}  # индекс → текст последней ошибки

    async def score_vacancy(self, vacancy: Vacancy, resume: Resume) -> ScoreResult:
        """Оценивает вакансию первым доступным провайдером цепочки."""
        return await self._call(lambda s: s.score_vacancy(vacancy, resume))

    async def write_cover_letter(
        self, vacancy: Vacancy, resume: Resume, score: ScoreResult
    ) -> str:
        """Пишет письмо первым доступным провайдером цепочки."""
        return await self._call(lambda s: s.write_cover_letter(vacancy, resume, score))

    async def _call(self, op: Callable[[OpenAICompatScorer], Awaitable[T]]) -> T:
        """Перебор провайдеров: пропустить остывающих, на ошибке — следующий."""
        now = self._clock()
        candidates = [
            i for i in range(len(self._scorers))
            if self._cooldown_until.get(i, 0.0) <= now
        ]
        if not candidates:
            # Все в cooldown — лимиты бывают консервативнее реальности, поэтому
            # пробуем того, кто остынет раньше всех, а не сдаёмся сразу.
            soonest = min(
                range(len(self._scorers)),
                key=lambda i: self._cooldown_until.get(i, 0.0),
            )
            logger.warning(
                "Все LLM-провайдеры в cooldown — пробую %s (остынет раньше всех)",
                self._scorers[soonest].name,
            )
            candidates = [soonest]
        for i in candidates:
            scorer = self._scorers[i]
            try:
                return await op(scorer)
            except ProviderUnavailableError as exc:
                self._cooldown_until[i] = self._clock() + exc.cooldown_seconds
                self._last_error[i] = str(exc)
                logger.warning(
                    "Провайдер %s недоступен: %s — cooldown %.0f с, переключаюсь на следующего",
                    scorer.name, exc, exc.cooldown_seconds,
                )
            except ScoringError as exc:
                self._last_error[i] = str(exc)
                logger.warning(
                    "Провайдер %s вернул ошибку: %s — переключаюсь на следующего",
                    scorer.name, exc,
                )
        raise ScoringError(
            "Все LLM-провайдеры недоступны: "
            + "; ".join(
                f"{s.name}: {self._last_error.get(i, 'в cooldown')}"
                for i, s in enumerate(self._scorers)
            )
        )
