"""Точка входа: поднимает веб-интерфейс (FastAPI + React-SPA) с поллером внутри.

Запуск из корня проекта: python -m hh_agent.main

Поллер живёт в lifespan приложения (web/app.py). make_scorer переэкспортируется
из composition — прежний путь импорта (hh_agent.main.make_scorer) сохранён.
"""

from __future__ import annotations

import logging

from hh_agent.composition import make_scorer  # noqa: F401  (re-export для тестов)
from hh_agent.config import Settings

__all__ = ["make_scorer", "main"]


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    settings = Settings()
    # workers=1 обязателен: одно sqlite-соединение, один APScheduler и модульный
    # флаг состояния прохода. reload в проде тоже нельзя — иначе двойной поллер.
    import uvicorn

    uvicorn.run(
        "hh_agent.web.app:app",
        host=settings.web_host,
        port=settings.web_port,
        workers=1,
        log_config=None,  # не перетираем формат из basicConfig
    )


if __name__ == "__main__":
    main()
