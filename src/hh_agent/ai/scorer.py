"""Скоринг вакансий и генерация сопроводительных писем через Anthropic API."""

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any, TypeVar

import pydantic
from anthropic import (
    APIConnectionError,
    APIError,
    APIStatusError,
    AsyncAnthropic,
    RateLimitError,
)

from ..config import Settings
from ..models import Resume, ScoreResult, Vacancy
from . import prompts

T = TypeVar("T")

_MAX_TOKENS = 16000

_STOP_REASON_ERRORS = {
    "refusal": (
        "Модель отказалась обрабатывать запрос (stop_reason=refusal). "
        "Проверьте содержимое вакансии и резюме."
    ),
    "max_tokens": (
        "Ответ модели оборван по лимиту токенов (stop_reason=max_tokens) — "
        "результат неполный и не может быть использован."
    ),
}


class ScoringError(RuntimeError):
    """Ошибка ИИ-модуля: API недоступен или ответ модели непригоден."""


class ClaudeScorer:
    """Реализация ScorerProto поверх Claude: structured outputs + adaptive thinking."""

    def __init__(self, settings: Settings) -> None:
        # Пустой ключ в настройках — SDK сам возьмёт ANTHROPIC_API_KEY из окружения.
        self._client = (
            AsyncAnthropic(api_key=settings.anthropic_api_key)
            if settings.anthropic_api_key
            else AsyncAnthropic()
        )
        self._model = settings.claude_model

    async def score_vacancy(self, vacancy: Vacancy, resume: Resume) -> ScoreResult:
        """Оценивает вакансию против резюме, возвращает структурированный вердикт."""
        response = await self._request(
            self._client.messages.parse(
                model=self._model,
                max_tokens=_MAX_TOKENS,
                thinking={"type": "adaptive"},
                system=prompts.SCORING_SYSTEM,
                messages=[
                    {"role": "user", "content": prompts.scoring_user_prompt(vacancy, resume)}
                ],
                output_format=ScoreResult,
            )
        )
        self._check_stop_reason(response.stop_reason)
        result = response.parsed_output
        if result is None:
            raise ScoringError("Модель не вернула структурированный результат скоринга.")
        return result

    async def write_cover_letter(
        self, vacancy: Vacancy, resume: Resume, score: ScoreResult
    ) -> str:
        """Пишет сопроводительное письмо; возвращает чистый текст без разметки."""
        response = await self._request(
            self._client.messages.create(
                model=self._model,
                max_tokens=_MAX_TOKENS,
                thinking={"type": "adaptive"},
                system=prompts.LETTER_SYSTEM,
                messages=[
                    {"role": "user", "content": prompts.letter_user_prompt(vacancy, resume, score)}
                ],
            )
        )
        self._check_stop_reason(response.stop_reason)
        text = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        if not text:
            raise ScoringError("Модель вернула пустое сопроводительное письмо.")
        return text

    async def _request(self, coro: Coroutine[Any, Any, T]) -> T:
        """Выполняет запрос к API, переводя типизированные ошибки SDK в ScoringError."""
        try:
            return await coro
        except pydantic.ValidationError as exc:
            # messages.parse валидирует ответ модели внутри вызова
            raise ScoringError(
                "Модель вернула ответ, который не удалось разобрать как ScoreResult."
            ) from exc
        except RateLimitError as exc:
            raise ScoringError(
                "Anthropic API: превышен лимит запросов (429), попробуйте позже."
            ) from exc
        except APIStatusError as exc:
            raise ScoringError(
                f"Anthropic API вернул ошибку {exc.status_code}: {exc.message}"
            ) from exc
        except APIConnectionError as exc:
            raise ScoringError(
                "Не удалось соединиться с Anthropic API — проверьте сеть и ключ."
            ) from exc
        except APIError as exc:  # прочие ошибки SDK — на случай новых подклассов
            raise ScoringError(f"Ошибка Anthropic API: {exc}") from exc

    @staticmethod
    def _check_stop_reason(stop_reason: str | None) -> None:
        message = _STOP_REASON_ERRORS.get(stop_reason or "")
        if message is not None:
            raise ScoringError(message)
