"""Хендлеры команд и callback-кнопок. Доступ — только для владельца бота."""

from __future__ import annotations

import html
import logging
from collections import Counter

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from ..interfaces import BotDeps
from ..models import Application, SearchQuery

logger = logging.getLogger(__name__)

START_TEXT = (
    "👋 Привет! Я — твой агент по поиску работы на hh.ru.\n\n"
    "Я слежу за новыми вакансиями по сохранённым поискам, оцениваю каждую против "
    "твоего резюме и присылаю карточки с разбором и готовым сопроводительным письмом.\n"
    "Откликаться нужно самому: открой вакансию по кнопке-ссылке, отправь отклик на "
    "сайте hh.ru и вернись нажать «✅ Откликнулся» — я запишу его в воронку.\n\n"
    "<b>Команды:</b>\n"
    "/searches — список поисковых запросов\n"
    "/add текст — добавить поиск (например: /add python разработчик)\n"
    "/del id — выключить поиск по его номеру\n"
    "/status — воронка откликов"
)


async def _is_owner(event: Message | CallbackQuery, deps: BotDeps) -> bool:
    """Молча пропускает только сообщения владельца (settings.telegram_chat_id).

    Предполагается приватный чат: там id пользователя совпадает с id чата.
    """
    user = event.from_user
    return user is not None and user.id == deps.settings.telegram_chat_id


# --- команды -----------------------------------------------------------------


async def cmd_start(message: Message) -> None:
    await message.answer(START_TEXT)


async def cmd_searches(message: Message, deps: BotDeps) -> None:
    searches = await deps.storage.list_searches(only_active=False)
    if not searches:
        await message.answer("Поисков пока нет. Добавь первый: /add python разработчик")
        return
    lines = ["🔎 <b>Поисковые запросы:</b>"]
    for s in searches:
        mark = "🟢" if s.active else "⚪"
        suffix = "" if s.active else " (выключен)"
        lines.append(f"{mark} #{s.id} {html.escape(s.text)}{suffix}")
    await message.answer("\n".join(lines))


async def cmd_add(message: Message, command: CommandObject, deps: BotDeps) -> None:
    text = (command.args or "").strip()
    if not text:
        await message.answer("Напиши текст поиска после команды: /add python разработчик")
        return
    saved = await deps.storage.add_search(SearchQuery(text=text))
    await message.answer(
        f"✅ Добавил поиск #{saved.id}: «{html.escape(saved.text)}». "
        "Начну проверять его в ближайший проход."
    )


async def cmd_del(message: Message, command: CommandObject, deps: BotDeps) -> None:
    arg = (command.args or "").strip()
    if not arg.isdigit():
        await message.answer("Укажи номер поиска: /del 1 (номера — в /searches)")
        return
    await deps.storage.deactivate_search(int(arg))
    await message.answer(f"⏹ Поиск #{arg} выключен. Список — в /searches")


async def cmd_status(message: Message, deps: BotDeps) -> None:
    """Локальная воронка откликов (соискательский API hh закрыт — данных hh нет)."""
    apps = await deps.storage.list_applications()
    lines = ["📊 <b>Воронка откликов</b>"]
    if apps:
        counts = Counter(app.state for app in apps)
        lines.append(f"\nОтмечено откликов: {len(apps)}")
        lines.extend(f"• {html.escape(state)}: {n}" for state, n in counts.most_common())
    else:
        lines.append(
            "\nОткликов пока нет — отмечай их кнопкой «✅ Откликнулся» на карточках, "
            "и они появятся здесь."
        )
    await message.answer("\n".join(lines))


async def fallback(message: Message) -> None:
    await message.answer("Не узнаю эту команду. Список того, что я умею, — в /start")


# --- callback-кнопки карточки -------------------------------------------------


def _card_markup(callback: CallbackQuery) -> InlineKeyboardMarkup | None:
    """Текущая клавиатура карточки (None, если сообщение недоступно)."""
    message = callback.message
    return message.reply_markup if isinstance(message, Message) else None


def _url_rows_only(markup: InlineKeyboardMarkup | None) -> InlineKeyboardMarkup | None:
    """Снимает callback-кнопки, оставляя ряды с URL-кнопками (ссылка на hh остаётся)."""
    if markup is None:
        return None
    rows = [row for row in markup.inline_keyboard if all(b.url for b in row)]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def _without_fav(markup: InlineKeyboardMarkup | None) -> InlineKeyboardMarkup | None:
    """Убирает кнопку «В избранное», остальные кнопки сохраняет."""
    if markup is None:
        return None
    rows = [
        [b for b in row if not (b.callback_data or "").startswith("fav:")]
        for row in markup.inline_keyboard
    ]
    rows = [row for row in rows if row]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def _finalize_card(
    callback: CallbackQuery, mark: str, reply_markup: InlineKeyboardMarkup | None = None
) -> None:
    """Дописывает пометку в карточку и меняет/убирает клавиатуру."""
    message = callback.message
    if not isinstance(message, Message) or not message.text:
        return
    try:
        await message.edit_text(f"{message.html_text}\n\n<b>{mark}</b>", reply_markup=reply_markup)
    except TelegramAPIError:
        # сбой редактирования не должен прерывать хендлер
        logger.warning("Не удалось отредактировать карточку", exc_info=True)


async def on_applied(callback: CallbackQuery, deps: BotDeps) -> None:
    """Фиксирует в воронке отклик, который человек уже отправил на сайте hh."""
    vacancy_id = (callback.data or "").split(":", 1)[1]
    letter = await deps.storage.get_letter(vacancy_id)  # None — не ошибка: письма могло не быть
    try:
        await deps.storage.save_application(
            Application(vacancy_id=vacancy_id, letter=letter or "", state="manual")
        )
    except Exception:
        logger.exception("Не удалось записать отклик по вакансии %s в воронку", vacancy_id)
        await callback.answer("Не получилось записать отклик — попробуй ещё раз.", show_alert=True)
        return
    # callback-кнопки снимаем, URL-кнопку оставляем — ссылка ещё пригодится
    await _finalize_card(
        callback, "✅ Отклик отмечен в воронке", reply_markup=_url_rows_only(_card_markup(callback))
    )
    await callback.answer("Записал в воронку")


async def on_skip(callback: CallbackQuery) -> None:
    # mark_seen уже сделан поллером — только обновляем карточку
    await _finalize_card(callback, "⏭ Пропущено")
    await callback.answer("Пропустили")


async def on_fav(callback: CallbackQuery, deps: BotDeps) -> None:
    vacancy_id = (callback.data or "").split(":", 1)[1]
    await deps.storage.set_favorite(vacancy_id)
    # ссылка и кнопки «Откликнулся»/«Пропустить» остаются — вакансия ещё в работе
    await _finalize_card(
        callback, "⭐ В избранном", reply_markup=_without_fav(_card_markup(callback))
    )
    await callback.answer("Добавлено в избранное")


def create_router() -> Router:
    """Собирает роутер бота; чужие апдейты отсекаются фильтром владельца."""
    router = Router(name="hh_agent_bot")
    router.message.filter(_is_owner)
    router.callback_query.filter(_is_owner)

    router.message.register(cmd_start, Command("start"))
    router.message.register(cmd_searches, Command("searches"))
    router.message.register(cmd_add, Command("add"))
    router.message.register(cmd_del, Command("del"))
    router.message.register(cmd_status, Command("status"))
    router.message.register(fallback)

    router.callback_query.register(on_applied, F.data.startswith("applied:"))
    router.callback_query.register(on_skip, F.data.startswith("skip:"))
    router.callback_query.register(on_fav, F.data.startswith("fav:"))
    return router
