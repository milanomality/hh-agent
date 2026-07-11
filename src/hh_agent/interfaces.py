"""Контракты между модулями. КОНТРАКТ: изменяется только оркестратором.

Каждый модуль реализует свой Protocol; композиция происходит в main.py —
модули не импортируют реализации друг друга напрямую.

С 15.12.2025 соискательский API hh.ru закрыт: HHClientProto содержит только
открытые методы (поиск и карточка вакансии); резюме берётся из локального файла,
отклик совершает человек по URL-кнопке.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .config import Settings
from .models import (
    Application,
    Resume,
    ScoreResult,
    SearchQuery,
    Vacancy,
)


class HHClientProto(Protocol):
    """Клиент открытого API hh.ru (модуль hh/)."""

    async def search_vacancies(
        self, query: SearchQuery, *, date_from: str | None = None
    ) -> list[Vacancy]: ...

    async def get_vacancy(self, vacancy_id: str) -> Vacancy: ...


class ScorerProto(Protocol):
    """ИИ-модуль: скоринг и письма (модуль ai/)."""

    async def score_vacancy(self, vacancy: Vacancy, resume: Resume) -> ScoreResult: ...

    async def write_cover_letter(
        self, vacancy: Vacancy, resume: Resume, score: ScoreResult
    ) -> str: ...


class StorageProto(Protocol):
    """Хранилище SQLite (модуль db/)."""

    async def init(self) -> None: ...

    # дедупликация вакансий
    async def is_seen(self, vacancy_id: str) -> bool: ...
    async def mark_seen(self, vacancy_id: str, score: ScoreResult | None = None) -> None: ...
    async def set_favorite(self, vacancy_id: str, fav: bool = True) -> None: ...

    # сопроводительные письма (пишет поллер, читает бот при отметке отклика)
    async def save_letter(self, vacancy_id: str, letter: str) -> None: ...
    async def get_letter(self, vacancy_id: str) -> str | None: ...

    # поисковые запросы
    async def list_searches(self, only_active: bool = True) -> list[SearchQuery]: ...
    async def add_search(self, query: SearchQuery) -> SearchQuery: ...
    async def deactivate_search(self, search_id: int) -> None: ...
    async def touch_search(
        self, search_id: int, polled_at: datetime | None = None
    ) -> None: ...  # None = текущее время; поллер передаёт момент НАЧАЛА запроса

    # локальная воронка откликов (отклик совершает человек на сайте hh)
    async def save_application(self, app: Application) -> None: ...
    async def list_applications(self) -> list[Application]: ...


class NotifierProto(Protocol):
    """Канал уведомлений пользователю (реализует модуль bot/)."""

    async def send_vacancy_card(
        self, vacancy: Vacancy, score: ScoreResult, letter: str | None
    ) -> None: ...

    async def send_text(self, text: str) -> None: ...


@dataclass
class BotDeps:
    """Зависимости, которые Telegram-бот получает при создании."""

    storage: StorageProto
    scorer: ScorerProto
    settings: Settings
