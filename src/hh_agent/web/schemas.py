"""Pydantic-модели ввода/вывода REST API. Доменные модели (Card, Event,
SearchQuery) отдаются напрямую как response_model — здесь только тела запросов
и составные ответы."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from ..models import Card


class LoginIn(BaseModel):
    password: str


class SessionOut(BaseModel):
    authenticated: bool


class FavoriteIn(BaseModel):
    favorite: bool = True


class SearchIn(BaseModel):
    text: str = Field(min_length=1)
    area: str | None = None
    salary_from: int | None = None
    experience: str | None = None
    schedule: str | None = None


class CardsPage(BaseModel):
    items: list[Card]
    total: int


class FunnelOut(BaseModel):
    applications_total: int
    by_state: dict[str, int]
    cards_by_status: dict[str, int]


class StatusOut(BaseModel):
    last_poll_at: datetime | None
    pass_failed: bool
    hh_token_present: bool
    hh_creds_present: bool
