"""Notifier для веб-интерфейса: вместо отправки в Telegram — персист в БД.

Реализует NotifierProto, поэтому поллер (scheduler.py) о смене канала не знает:
карточки складываются в таблицу cards, системные сообщения — в events, а веб-фронт
читает их через REST API.
"""

from __future__ import annotations

import html

from ..interfaces import StorageProto
from ..models import ScoreResult, Vacancy


class WebNotifier:
    """Складывает карточки и системные уведомления в хранилище (StorageProto)."""

    def __init__(self, storage: StorageProto) -> None:
        self._storage = storage

    async def send_vacancy_card(
        self,
        vacancy: Vacancy,
        score: ScoreResult,
        letter: str | None,
        *,
        search_id: int | None = None,
    ) -> None:
        await self._storage.save_card(vacancy, score, letter, search_id=search_id)

    async def send_text(self, text: str) -> None:
        # poll_once пре-экранирует свои алерты под Telegram-HTML (html.escape);
        # React экранирует сам, поэтому храним чистый текст, снимая экранирование.
        await self._storage.add_event(html.unescape(text))
