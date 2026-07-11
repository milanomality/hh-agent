"""Скоринг и письма через OpenAI-совместимые API (Groq / OpenRouter / Gemini / Ollama).

Серверной гарантии схемы у бесплатных провайдеров нет, поэтому: JSON-режим
(response_format, где поддерживается) + схема в системном промпте +
pydantic-валидация с одним ретраем; невалидный ответ — ScoringError.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlparse

import pydantic
from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    AsyncOpenAI,
    BadRequestError,
    RateLimitError,
)

from ..config import Settings
from ..models import Resume, ScoreResult, Vacancy
from . import prompts
from .scorer import ScoringError

logger = logging.getLogger(__name__)

# Безопасный потолок для всех compat-провайдеров (у многих лимит ниже 8k).
_MAX_TOKENS = 4096

# Дефолтные cooldown, если провайдер не подсказал retry-after: исчерпанная
# квота (429) держится дольше, чем сбой сервера или сети.
_COOLDOWN_RATE_LIMIT = 900.0
_COOLDOWN_SERVER = 300.0

# model_validate_json при битом JSON бросает ValidationError (pydantic v2),
# JSONDecodeError оставлен на случай прямого json-парсинга в будущем.
_PARSE_ERRORS = (json.JSONDecodeError, pydantic.ValidationError)


class ProviderUnavailableError(ScoringError):
    """Провайдер временно недоступен (429 / 5xx / сеть) — кандидат на cooldown."""

    def __init__(self, message: str, *, cooldown_seconds: float) -> None:
        super().__init__(message)
        self.cooldown_seconds = cooldown_seconds


def _retry_after_seconds(exc: RateLimitError) -> float:
    """Cooldown из заголовка retry-after (число секунд); нет/не число — дефолт."""
    header = exc.response.headers.get("retry-after")
    if header is not None:
        try:
            seconds = float(header)
        except ValueError:  # HTTP-дата или мусор — не разбираем
            return _COOLDOWN_RATE_LIMIT
        if seconds >= 0:
            return seconds
    return _COOLDOWN_RATE_LIMIT


def _strip_fences(text: str) -> str:
    """Срезает markdown-фенсы (```json ... ```), если модель обернула ответ."""
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()[1:]  # открывающая строка ``` или ```json
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_json(text: str) -> str:
    """Достаёт JSON-объект из ответа: фенсы и мусор вокруг {...} отбрасываются."""
    text = _strip_fences(text)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text


class OpenAICompatScorer:
    """Реализация ScorerProto поверх OpenAI-совместимого API (base_url + model из настроек)."""

    def __init__(
        self,
        settings: Settings,
        *,
        name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        # Overrides — для failover-цепочки (записи providers.json); None → settings.
        base_url = base_url or settings.llm_base_url
        if api_key is None:
            api_key = settings.llm_api_key
        # "unused" — для Ollama: ключ ей не нужен, но SDK требует непустую строку.
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key or "unused")
        self._model = model or settings.llm_model
        self.name = name or (urlparse(base_url).hostname or base_url)
        self._json_mode = True  # сбрасывается, если провайдер отверг response_format

    async def score_vacancy(self, vacancy: Vacancy, resume: Resume) -> ScoreResult:
        """Оценивает вакансию против резюме; невалидный JSON — один ретрай с текстом ошибки."""
        messages = [
            {"role": "system", "content": prompts.scoring_system_json()},
            {"role": "user", "content": prompts.scoring_user_prompt(vacancy, resume)},
        ]
        content = await self._complete(messages, json_mode=True)
        try:
            return self._parse_score(content)
        except _PARSE_ERRORS as exc:
            logger.warning("Невалидный ответ модели при скоринге, повторяю запрос: %s", exc)
            messages = [
                *messages,
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": (
                        f"Твой ответ не прошёл валидацию:\n{exc}\n\n"
                        "Исправь и верни ТОЛЬКО валидный JSON-объект по схеме из "
                        "системного сообщения, без пояснений и markdown-разметки."
                    ),
                },
            ]
            content = await self._complete(messages, json_mode=True)
            try:
                return self._parse_score(content)
            except _PARSE_ERRORS as exc_retry:
                raise ScoringError(
                    "Модель дважды вернула ответ, который не удалось разобрать как "
                    "результат скоринга. Попробуйте другую модель (LLM_MODEL)."
                ) from exc_retry

    async def write_cover_letter(
        self, vacancy: Vacancy, resume: Resume, score: ScoreResult
    ) -> str:
        """Пишет сопроводительное письмо; возвращает чистый текст без разметки."""
        content = await self._complete(
            [
                {"role": "system", "content": prompts.LETTER_SYSTEM},
                {"role": "user", "content": prompts.letter_user_prompt(vacancy, resume, score)},
            ]
        )
        text = _strip_fences(content)
        if not text:
            raise ScoringError("Модель вернула пустое сопроводительное письмо.")
        return text

    async def _complete(
        self, messages: list[dict[str, str]], *, json_mode: bool = False
    ) -> str:
        """Запрос chat.completions; ошибки SDK переводятся в ScoringError.

        429 / 5xx / сетевые — ProviderUnavailableError с cooldown (сигнал
        failover-цепочке увести провайдера в паузу), прочие — ScoringError.

        В JSON-режиме пробует response_format={"type": "json_object"}; на 400
        повторяет без него (один раз) и больше этот параметр не отправляет.
        """
        try:
            if json_mode and self._json_mode:
                try:
                    response = await self._create(
                        messages, response_format={"type": "json_object"}
                    )
                    return self._content(response)
                except BadRequestError:
                    self._json_mode = False
                    logger.info(
                        "Провайдер отклонил response_format=json_object — повторяю без него"
                    )
            return self._content(await self._create(messages))
        except RateLimitError as exc:
            raise ProviderUnavailableError(
                "LLM API: превышен лимит запросов (429), попробуйте позже.",
                cooldown_seconds=_retry_after_seconds(exc),
            ) from exc
        except APIStatusError as exc:
            if exc.status_code >= 500:
                raise ProviderUnavailableError(
                    f"LLM API вернул ошибку {exc.status_code}: {exc.message}",
                    cooldown_seconds=_COOLDOWN_SERVER,
                ) from exc
            raise ScoringError(
                f"LLM API вернул ошибку {exc.status_code}: {exc.message}"
            ) from exc
        except APIConnectionError as exc:
            raise ProviderUnavailableError(
                "Не удалось соединиться с LLM API — проверьте LLM_BASE_URL и сеть.",
                cooldown_seconds=_COOLDOWN_SERVER,
            ) from exc
        except APIError as exc:  # прочие ошибки SDK — на случай новых подклассов
            raise ScoringError(f"Ошибка LLM API: {exc}") from exc

    async def _create(self, messages: list[dict[str, str]], **extra: Any) -> Any:
        """Сырой вызов chat.completions.create (без перевода ошибок)."""
        return await self._client.chat.completions.create(
            model=self._model,
            messages=messages,  # type: ignore[arg-type]
            max_tokens=_MAX_TOKENS,
            **extra,
        )

    @staticmethod
    def _content(response: Any) -> str:
        """Текст первого варианта ответа; пустой ответ провайдера — ScoringError."""
        if not response.choices:
            raise ScoringError("LLM API вернул ответ без вариантов (choices пуст).")
        content = response.choices[0].message.content
        if content is None:
            raise ScoringError("Модель вернула пустой ответ (content отсутствует).")
        return content

    @staticmethod
    def _parse_score(content: str) -> ScoreResult:
        """Валидирует ответ модели как ScoreResult (фенсы и мусор срезаются)."""
        return ScoreResult.model_validate_json(_extract_json(content))
