"""Авторизация веб-интерфейса: единый пароль → подписанная сессионная кука.

Один пользователь, один пароль — полноценный OAuth/JWT избыточен. Сессия хранится
в подписанной куке (Starlette SessionMiddleware поверх itsdangerous)."""

from __future__ import annotations

from fastapi import HTTPException, Request


def is_authenticated(request: Request) -> bool:
    return bool(request.session.get("auth"))


def require_auth(request: Request) -> None:
    """Зависимость-страж для защищённых роутов: 401, если сессия не авторизована."""
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Требуется авторизация")
