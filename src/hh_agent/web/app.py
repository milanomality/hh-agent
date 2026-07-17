"""FastAPI-приложение: REST API + отдача собранного React-SPA.

Композиция ресурсов и жизненный цикл поллера живут в lifespan (запускается на
event loop uvicorn). Один процесс, один воркер — так требует единственное
sqlite-соединение, единственный APScheduler и модульный флаг _last_pass_failed.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from ..composition import make_scorer
from ..config import Settings
from ..db.storage import Storage
from ..hh.client import HHClient
from ..scheduler import setup_scheduler
from .auth import require_auth
from .notifier import WebNotifier
from .routes import api, public

logger = logging.getLogger(__name__)


def _validate_settings(settings: Settings) -> None:
    missing = [
        name
        for name, value in (("WEB_PASSWORD", settings.web_password), ("WEB_SECRET", settings.web_secret))
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"Не заданы {', '.join(missing)} в .env — задайте перед запуском веб-интерфейса "
            "(WEB_SECRET сгенерируйте: python -c \"import secrets;print(secrets.token_urlsafe(32))\")."
        )


def _frontend_dist(settings: Settings) -> Path:
    if settings.frontend_dist:
        return Path(settings.frontend_dist)
    # src/hh_agent/web/app.py → корень репозитория → frontend/dist
    return Path(__file__).resolve().parents[3] / "frontend" / "dist"


def create_app(
    *,
    settings: Settings | None = None,
    storage: Storage | None = None,
    start_scheduler: bool = True,
) -> FastAPI:
    """Собирает приложение. Для тестов: инъекция storage + start_scheduler=False
    (без сети и APScheduler); в бою — всё создаётся в lifespan."""
    settings = settings or Settings()
    injected_storage = storage

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _validate_settings(settings)
        st = injected_storage or Storage(settings.db_path)
        hh: HHClient | None = None
        scheduler = None
        app.state.settings = settings
        app.state.storage = st
        app.state.hh = None
        app.state.scheduler = None
        try:
            await st.init()  # идемпотентно; на инъектированном storage безопасно
            hh = HHClient(settings)
            app.state.hh = hh
            if start_scheduler:
                scorer = make_scorer(settings)  # только когда реально поллим
                notifier = WebNotifier(st)
                scheduler = setup_scheduler(hh, scorer, st, notifier, settings)
                scheduler.start()  # первый poll_once — сразу (next_run_time=now)
                app.state.scheduler = scheduler
                logger.info("Поллер запущен (интервал %d мин)", settings.poll_interval_minutes)
            yield
        finally:
            # порядок: сначала стопаем джобы, потом закрываем ресурсы
            if scheduler is not None:
                scheduler.shutdown(wait=True)
            if hh is not None:
                await hh.close()
            if injected_storage is None:  # закрываем только то, что создали сами
                await st.close()

    app = FastAPI(title="hh-agent", lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.web_secret or "dev-insecure-secret",
        https_only=settings.web_secure_cookie,
        same_site="lax",
    )
    app.include_router(public, prefix="/api")
    app.include_router(api, prefix="/api", dependencies=[Depends(require_auth)])
    _mount_frontend(app, settings)
    return app


def _mount_frontend(app: FastAPI, settings: Settings) -> None:
    """Отдаёт собранный SPA с корня. Монтируется ПОСЛЕ /api, чтобы не затенять API;
    html=True — SPA-фолбэк на index.html для клиентских маршрутов."""
    dist = _frontend_dist(settings)
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="spa")
    else:
        logger.warning(
            "Собранный фронтенд не найден (%s). Соберите: cd frontend && npm ci && npm run build. "
            "Пока доступен только /api.",
            dist,
        )


app = create_app()
