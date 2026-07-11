"""Сборка Telegram-бота: Bot + Dispatcher + Notifier (контракт для main.py)."""

from __future__ import annotations

import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from ..interfaces import BotDeps, NotifierProto
from .handlers import create_router
from .notifier import TelegramNotifier


def _session_from_env() -> AiohttpSession | None:
    """aiohttp, в отличие от httpx, не читает HTTPS_PROXY/HTTP_PROXY сам —
    без этого при системном прокси/VPN бот не достучится до api.telegram.org."""
    proxy = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")  # на Linux/macOS распространён lowercase
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
    )
    return AiohttpSession(proxy=proxy) if proxy else None


def create_bot(deps: BotDeps) -> tuple[Bot, Dispatcher, NotifierProto]:
    """Создаёт бота с HTML-разметкой по умолчанию и зарегистрированными хендлерами."""
    bot = Bot(
        token=deps.settings.telegram_bot_token,
        session=_session_from_env(),
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
            link_preview_is_disabled=True,  # карточки и так длинные — превью ссылок не нужны
        ),
    )
    dispatcher = Dispatcher(deps=deps)  # deps инжектится в хендлеры по имени аргумента
    dispatcher.include_router(create_router())
    notifier = TelegramNotifier(bot, deps.settings.telegram_chat_id)
    return bot, dispatcher, notifier
