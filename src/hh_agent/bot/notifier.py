"""Отправка уведомлений пользователю в Telegram (реализация NotifierProto)."""

from __future__ import annotations

from aiogram import Bot

from ..models import ScoreResult, Vacancy
from .cards import render_vacancy_card, vacancy_keyboard


class TelegramNotifier:
    """Шлёт карточки вакансий и тексты в личный чат пользователя."""

    def __init__(self, bot: Bot, chat_id: int) -> None:
        self._bot = bot
        self._chat_id = chat_id

    async def send_vacancy_card(
        self, vacancy: Vacancy, score: ScoreResult, letter: str | None
    ) -> None:
        await self._bot.send_message(
            self._chat_id,
            render_vacancy_card(vacancy, score, letter),
            reply_markup=vacancy_keyboard(vacancy),
        )

    async def send_text(self, text: str) -> None:
        """Шлёт текст как есть; у бота parse_mode=HTML, поэтому < > & нужно экранировать заранее."""
        await self._bot.send_message(self._chat_id, text)
