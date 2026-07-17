"""REST-эндпоинты веб-интерфейса. Всё под /api; публичные (login/session/healthz)
и защищённые (остальное) роутеры собираются в app.py."""

from __future__ import annotations

import hmac
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..config import Settings
from ..hh.client import HHClient
from ..interfaces import StorageProto
from ..models import Application, Card, CardStatus, Event, SearchQuery
from ..scheduler import get_pass_failed
from .auth import is_authenticated, require_auth
from .schemas import (
    CardsPage,
    FavoriteIn,
    FunnelOut,
    LoginIn,
    SearchIn,
    SessionOut,
    StatusOut,
)


def _storage(request: Request) -> StorageProto:
    return request.app.state.storage


def _hh(request: Request) -> HHClient:
    return request.app.state.hh


def _settings(request: Request) -> Settings:
    return request.app.state.settings


# ── публичные роуты (без авторизации) ────────────────────────────────────────

public = APIRouter()


@public.post("/login", status_code=204)
async def login(body: LoginIn, request: Request, settings: Settings = Depends(_settings)) -> None:
    # constant-time сравнение по байтам (compare_digest на str с не-ASCII бросает
    # TypeError — кириллический пароль иначе давал бы 500); пустой пароль → вход закрыт
    if not settings.web_password or not hmac.compare_digest(
        body.password.encode("utf-8"), settings.web_password.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Неверный пароль")
    request.session["auth"] = True


@public.post("/logout", status_code=204)
async def logout(request: Request) -> None:
    request.session.clear()


@public.get("/session", response_model=SessionOut)
async def session(request: Request) -> SessionOut:
    return SessionOut(authenticated=is_authenticated(request))


@public.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


# ── защищённые роуты (dependencies=[require_auth] навешивается в app.py) ──────

api = APIRouter()


async def _card_or_404(storage: StorageProto, vacancy_id: str) -> Card:
    card = await storage.get_card(vacancy_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    return card


@api.get("/cards", response_model=CardsPage)
async def list_cards(
    min_score: int | None = None,
    favorite: bool | None = None,
    status: CardStatus | None = None,
    search_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    storage: StorageProto = Depends(_storage),
) -> CardsPage:
    filters = dict(min_score=min_score, favorite=favorite, status=status, search_id=search_id)
    items = await storage.list_cards(**filters, limit=limit, offset=offset)
    total = await storage.count_cards(**filters)
    return CardsPage(items=items, total=total)


@api.get("/cards/{vacancy_id}", response_model=Card)
async def get_card(vacancy_id: str, storage: StorageProto = Depends(_storage)) -> Card:
    return await _card_or_404(storage, vacancy_id)


@api.post("/cards/{vacancy_id}/applied", response_model=Card)
async def mark_applied(vacancy_id: str, storage: StorageProto = Depends(_storage)) -> Card:
    card = await _card_or_404(storage, vacancy_id)
    # идемпотентно: повторный POST не плодит записи в воронке и не сдвигает applied_at
    if card.status != CardStatus.applied:
        await storage.mark_card_applied(vacancy_id)
        # фиксируем факт в воронке откликов (как делал бот на кнопке «Откликнулся»)
        await storage.save_application(
            Application(vacancy_id=vacancy_id, letter=card.letter or "", state="manual")
        )
    return await _card_or_404(storage, vacancy_id)


@api.post("/cards/{vacancy_id}/skip", response_model=Card)
async def mark_skip(vacancy_id: str, storage: StorageProto = Depends(_storage)) -> Card:
    await _card_or_404(storage, vacancy_id)
    await storage.mark_card_skipped(vacancy_id)
    return await _card_or_404(storage, vacancy_id)


@api.post("/cards/{vacancy_id}/favorite", response_model=Card)
async def set_favorite(
    vacancy_id: str, body: FavoriteIn, storage: StorageProto = Depends(_storage)
) -> Card:
    await _card_or_404(storage, vacancy_id)
    await storage.set_card_favorite(vacancy_id, body.favorite)
    return await _card_or_404(storage, vacancy_id)


@api.get("/searches", response_model=list[SearchQuery])
async def list_searches(
    include_inactive: bool = False, storage: StorageProto = Depends(_storage)
) -> list[SearchQuery]:
    return await storage.list_searches(only_active=not include_inactive)


@api.post("/searches", response_model=SearchQuery, status_code=201)
async def add_search(body: SearchIn, storage: StorageProto = Depends(_storage)) -> SearchQuery:
    return await storage.add_search(SearchQuery(**body.model_dump()))


@api.post("/searches/{search_id}/deactivate", status_code=204)
async def deactivate_search(search_id: int, storage: StorageProto = Depends(_storage)) -> None:
    await storage.deactivate_search(search_id)


@api.get("/funnel", response_model=FunnelOut)
async def funnel(storage: StorageProto = Depends(_storage)) -> FunnelOut:
    apps = await storage.list_applications()
    by_state = dict(Counter(a.state for a in apps))
    cards_by_status = {s.value: await storage.count_cards(status=s) for s in CardStatus}
    return FunnelOut(
        applications_total=len(apps), by_state=by_state, cards_by_status=cards_by_status
    )


@api.get("/events", response_model=list[Event])
async def events(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    storage: StorageProto = Depends(_storage),
) -> list[Event]:
    return await storage.list_events(limit=limit, offset=offset)


@api.get("/status", response_model=StatusOut)
async def status(
    storage: StorageProto = Depends(_storage), hh: HHClient = Depends(_hh)
) -> StatusOut:
    searches = await storage.list_searches(only_active=False)
    polls = [s.last_polled_at for s in searches if s.last_polled_at]
    return StatusOut(
        last_poll_at=max(polls) if polls else None,
        pass_failed=get_pass_failed(),
        hh_token_present=hh.has_app_token,
        hh_creds_present=hh.has_app_creds,
    )
