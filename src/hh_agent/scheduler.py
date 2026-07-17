"""Поллер вакансий (сердце системы) и настройка расписания APScheduler."""

from __future__ import annotations

import html
import logging
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import Settings
from .interfaces import HHClientProto, NotifierProto, ScorerProto, StorageProto
from .models import Resume, SearchQuery

logger = logging.getLogger(__name__)

_TITLE_MAX = 80

# Дедуп уведомления «весь проход упал» между проходами: шлём при переходе
# ok→fail и «починилось» при fail→ok, а не каждый интервал (см. ревью I1).
_last_pass_failed = False


def get_pass_failed() -> bool:
    """Упал ли последний проход поиска целиком (для /api/status)."""
    return _last_pass_failed


async def _read_resume(settings: Settings, notifier: NotifierProto) -> Resume | None:
    """Резюме из локального файла settings.resume_path; None — прервать проход.

    Соискательский API hh закрыт, поэтому резюме живёт в файле пользователя.
    """
    path = Path(settings.resume_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    if not text.strip():
        # у бота parse_mode=HTML — в подсказке нет символов < > &
        await notifier.send_text(
            f"Файл резюме «{settings.resume_path}» не найден или пуст.\n"
            "Создайте файл резюме (обычный текст или markdown) по этому пути "
            "и перезапустите агента."
        )
        return None
    title = next(line.strip() for line in text.splitlines() if line.strip())
    return Resume(id="local", title=title[:_TITLE_MAX], text=text)


async def _process_search(
    search: SearchQuery,
    resume: Resume,
    hh: HHClientProto,
    scorer: ScorerProto,
    storage: StorageProto,
    notifier: NotifierProto,
    settings: Settings,
) -> None:
    # Окно поиска сдвигаем на момент НАЧАЛА запроса, а не конца обработки:
    # вакансии, опубликованные во время скоринга, не выпадают из окна следующего
    # прохода. Упавшие вакансии не помечены is_seen — они обработаются повторно,
    # если снова попадут в выдачу.
    poll_started = datetime.now(timezone.utc)
    date_from = search.last_polled_at.isoformat() if search.last_polled_at else None
    found = await hh.search_vacancies(search, date_from=date_from)
    for stub in found:
        if await storage.is_seen(stub.id):
            continue
        try:
            vacancy = await hh.get_vacancy(stub.id)
            score = await scorer.score_vacancy(vacancy, resume)
            await storage.mark_seen(stub.id, score)  # всегда, независимо от оценки
            if score.score >= settings.score_threshold:
                letter = await scorer.write_cover_letter(vacancy, resume, score)
                await storage.save_letter(stub.id, letter)
                await notifier.send_vacancy_card(vacancy, score, letter, search_id=search.id)
        except Exception:
            logger.exception("Ошибка при обработке вакансии %s", stub.id)
    if search.id is not None:
        await storage.touch_search(search.id, polled_at=poll_started)


async def poll_once(
    hh: HHClientProto,
    scorer: ScorerProto,
    storage: StorageProto,
    notifier: NotifierProto,
    settings: Settings,
) -> None:
    """Один проход: новые вакансии по всем активным поискам → скоринг → письмо → карточка."""
    global _last_pass_failed
    resume = await _read_resume(settings, notifier)  # читаем один раз за проход
    if resume is None:
        return
    searches = await storage.list_searches()
    failures: list[str] = []
    for search in searches:
        try:
            await _process_search(search, resume, hh, scorer, storage, notifier, settings)
        except Exception as exc:
            # ошибка по поиску — last_polled_at не сдвигаем, окно повторится
            logger.exception("Ошибка при обработке поиска %r", search.text)
            failures.append(str(exc))
    all_failed = bool(searches) and len(failures) == len(searches)
    if all_failed and not _last_pass_failed:
        # системный сбой (все поиски упали) — молчать нельзя, пользователь ждёт карточек
        await notifier.send_text(
            "⚠️ Проход поиска не удался целиком.\n"
            f"Последняя ошибка: {html.escape(failures[-1][:400])}\n"
            f"Буду повторять каждые {settings.poll_interval_minutes} мин "
            "и сообщу, когда заработает."
        )
    elif _last_pass_failed and searches and not all_failed:
        await notifier.send_text("✅ Поиск снова работает.")
    if searches:
        _last_pass_failed = all_failed


def setup_scheduler(
    hh: HHClientProto,
    scorer: ScorerProto,
    storage: StorageProto,
    notifier: NotifierProto,
    settings: Settings,
) -> AsyncIOScheduler:
    """Шедулер с интервальной джобой poll_once; первый прогон — сразу при старте."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        poll_once,
        trigger="interval",
        minutes=settings.poll_interval_minutes,
        args=(hh, scorer, storage, notifier, settings),
        id="poll_once",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(),
    )
    return scheduler
