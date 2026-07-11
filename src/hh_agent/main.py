"""Composition root. Запуск: python -m hh_agent.main"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from urllib.parse import urlparse

from hh_agent.ai.fallback import FallbackScorer
from hh_agent.ai.openai_scorer import OpenAICompatScorer
from hh_agent.ai.scorer import ClaudeScorer
from hh_agent.bot.bot import create_bot
from hh_agent.config import Settings
from hh_agent.db.storage import Storage
from hh_agent.hh.client import HHClient
from hh_agent.interfaces import BotDeps, ScorerProto
from hh_agent.scheduler import setup_scheduler

logger = logging.getLogger(__name__)

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _make_provider_chain(settings: Settings, path: Path) -> FallbackScorer:
    """Строит failover-цепочку OpenAICompatScorer из providers.json."""
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(
            f"Ошибка: не удалось прочитать {path}: {exc}. "
            "Исправьте файл по образцу providers.json.example.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not isinstance(entries, list) or not entries:
        print(
            f"Ошибка: {path} должен содержать непустой JSON-список провайдеров "
            "(записи с полями name, base_url, api_key, model). "
            "Образец — providers.json.example.",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        # api_key может отсутствовать в записи (Ollama) — тогда "", а не LLM_API_KEY
        scorers = [
            OpenAICompatScorer(settings, **{"api_key": "", **entry}) for entry in entries
        ]
    except TypeError as exc:
        print(
            f"Ошибка: некорректная запись провайдера в {path}: {exc}. "
            "Допустимые поля: name, base_url, api_key, model. "
            "Образец — providers.json.example.",
            file=sys.stderr,
        )
        sys.exit(1)
    logger.info(
        "Failover-цепочка LLM-провайдеров (%d): %s",
        len(scorers),
        ", ".join(s.name for s in scorers),
    )
    return FallbackScorer(scorers)


def make_scorer(settings: Settings) -> ScorerProto:
    """Выбирает реализацию скорера по settings.llm_provider."""
    if settings.llm_provider == "anthropic":
        return ClaudeScorer(settings)
    if settings.llm_provider == "openai_compat":
        providers_file = Path(settings.llm_providers_file)
        if providers_file.exists():
            return _make_provider_chain(settings, providers_file)
        host = urlparse(settings.llm_base_url).hostname or ""
        if not settings.llm_api_key and host not in _LOCAL_HOSTS:
            # не падаем: локальной Ollama ключ не нужен, а облачный провайдер
            # сам вернёт понятную 401 — но предупредить стоит заранее
            logger.warning(
                "LLM_API_KEY не задан, а LLM_BASE_URL (%s) не локальный — облачный "
                "провайдер, скорее всего, отклонит запросы. Укажите ключ в .env.",
                settings.llm_base_url,
            )
        return OpenAICompatScorer(settings)
    print(
        f"Ошибка: неизвестный LLM_PROVIDER={settings.llm_provider!r}. "
        "Допустимые значения: openai_compat (бесплатные OpenAI-совместимые API) "
        "и anthropic.",
        file=sys.stderr,
    )
    sys.exit(1)


async def run() -> None:
    settings = Settings()
    if not settings.telegram_bot_token:
        print(
            "Ошибка: не задан TELEGRAM_BOT_TOKEN. Скопируйте .env.example в .env "
            "и укажите токен бота (получить у @BotFather).",
            file=sys.stderr,
        )
        sys.exit(1)
    if not settings.telegram_chat_id:
        logger.warning("TELEGRAM_CHAT_ID не задан — уведомления могут не доставляться")

    scorer = make_scorer(settings)  # при неизвестном LLM_PROVIDER выходим до открытия ресурсов
    storage = Storage(settings.db_path)
    await storage.init()
    hh = HHClient(settings)  # боту не нужен — только поллеру (поиск и карточки вакансий)
    bot, dp, notifier = create_bot(BotDeps(storage=storage, scorer=scorer, settings=settings))

    scheduler = setup_scheduler(hh, scorer, storage, notifier, settings)
    scheduler.start()  # первый прогон poll_once выполнится сразу (next_run_time=now)
    logger.info(
        "Поллер запущен (интервал %d мин); стартую Telegram-бота",
        settings.poll_interval_minutes,
    )
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=True)  # останавливаем приём новых джоб
        await hh.close()
        await storage.close()
        logger.info("Остановлено")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Завершение по Ctrl+C")


if __name__ == "__main__":
    main()
