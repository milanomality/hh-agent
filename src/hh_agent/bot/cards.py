"""Рендер карточки вакансии и inline-клавиатуры. Чистые функции, без I/O."""

from __future__ import annotations

import html

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..models import ScoreResult, Vacancy, Verdict

_VERDICT_RU = {
    Verdict.apply: "стоит откликнуться",
    Verdict.maybe: "может подойти",
    Verdict.skip: "лучше пропустить",
}


def _bullets(items: list[str], marker: str) -> list[str]:
    return [f"{marker} {html.escape(item)}" for item in items]


def render_vacancy_card(vacancy: Vacancy, score: ScoreResult, letter: str | None) -> str:
    """HTML-текст карточки вакансии для Telegram."""
    name = html.escape(vacancy.name)
    title = f'<b><a href="{html.escape(vacancy.url)}">{name}</a></b>' if vacancy.url else f"<b>{name}</b>"

    lines = [title]
    if vacancy.employer.name:
        lines.append(f"🏢 {html.escape(vacancy.employer.name)}")
    details = f"💰 {html.escape(vacancy.salary_text)}"
    if vacancy.area_name:
        details += f" · 📍 {html.escape(vacancy.area_name)}"
    lines.append(details)

    verdict = _VERDICT_RU.get(score.verdict, score.verdict.value)
    lines.append(f"\n<b>Оценка: {score.score}/10</b> ({verdict})")
    if score.summary:
        lines.append(html.escape(score.summary))

    marked = (
        _bullets(score.matches, "✅")
        + _bullets(score.gaps, "⚠️")
        + _bullets(score.red_flags, "🚩")
    )
    if marked:
        lines.append("")
        lines.extend(marked)

    if letter:
        lines.append(f"\n✉️ <b>Сопроводительное письмо:</b>\n<blockquote>{html.escape(letter)}</blockquote>")

    return "\n".join(lines)


def vacancy_keyboard(vacancy: Vacancy) -> InlineKeyboardMarkup:
    """Клавиатура карточки; формат callback_data зафиксирован в PLAN.md.

    Первый ряд — URL-кнопка на страницу вакансии (отклик совершает человек на сайте);
    без url ряд не добавляется. Второй — callback-кнопки локальных действий.
    """
    rows: list[list[InlineKeyboardButton]] = []
    if vacancy.url:
        rows.append([InlineKeyboardButton(text="🔗 Открыть на hh.ru", url=vacancy.url)])
    rows.append(
        [
            InlineKeyboardButton(text="✅ Откликнулся", callback_data=f"applied:{vacancy.id}"),
            InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"skip:{vacancy.id}"),
            InlineKeyboardButton(text="⭐ В избранное", callback_data=f"fav:{vacancy.id}"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
