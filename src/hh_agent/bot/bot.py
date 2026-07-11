"""Сборка Telegram-бота: Bot + Dispatcher + Notifier (контракт для main.py)."""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from ..interfaces import BotDeps, NotifierProto
from .handlers import create_router
from .notifier import TelegramNotifier


def create_bot(deps: BotDeps) -> tuple[Bot, Dispatcher, NotifierProto]:
    """Создаёт бота с HTML-разметкой по умолчанию и зарегистрированными хендлерами."""
    bot = Bot(
        token=deps.settings.telegram_bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
            link_preview_is_disabled=True,  # карточки и так длинные — превью ссылок не нужны
        ),
    )
    dispatcher = Dispatcher(deps=deps)  # deps инжектится в хендлеры по имени аргумента
    dispatcher.include_router(create_router())
    notifier = TelegramNotifier(bot, deps.settings.telegram_chat_id)
    return bot, dispatcher, notifier
