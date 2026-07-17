"""Общие модели данных. КОНТРАКТ: изменяется только оркестратором."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class SearchQuery(BaseModel):
    """Сохранённый поисковый запрос; поллер опрашивает его по расписанию."""

    id: int | None = None
    text: str
    area: str | None = None          # id региона hh (например "41" — Калининград)
    salary_from: int | None = None
    experience: str | None = None    # noExperience / between1And3 / ...
    schedule: str | None = None      # remote / fullDay / ...
    active: bool = True
    last_polled_at: datetime | None = None


class Employer(BaseModel):
    id: str | None = None
    name: str = ""


class Vacancy(BaseModel):
    id: str
    name: str
    employer: Employer = Employer()
    salary_text: str = "не указана"
    area_name: str = ""
    url: str = ""                    # alternate_url — ссылка для человека
    published_at: datetime | None = None
    description: str = ""            # полный текст без HTML (заполняет get_vacancy)
    key_skills: list[str] = Field(default_factory=list)


class Resume(BaseModel):
    id: str
    title: str = ""
    text: str = ""                   # резюме, отрендеренное в плоский текст для LLM


class Verdict(str, Enum):
    apply = "apply"
    maybe = "maybe"
    skip = "skip"


class ScoreResult(BaseModel):
    """Структурированный вердикт LLM по вакансии против резюме."""

    score: int = Field(ge=0, le=10)
    verdict: Verdict
    summary: str                                            # 1-2 предложения: почему такая оценка
    matches: list[str] = Field(default_factory=list)        # что совпадает с резюме
    gaps: list[str] = Field(default_factory=list)           # чего не хватает
    red_flags: list[str] = Field(default_factory=list)      # тревожные признаки вакансии


class Application(BaseModel):
    """Локальная запись об отклике (для воронки).

    Отклик совершает человек на сайте hh; веб фиксирует факт по кнопке «Откликнулся».
    """

    vacancy_id: str
    resume_id: str = "local"
    letter: str = ""
    state: str = "manual"
    created_at: datetime | None = None


class CardStatus(str, Enum):
    new = "new"
    applied = "applied"
    skipped = "skipped"


class Card(BaseModel):
    """Оценённая вакансия, сохранённая для веб-фида.

    Контент, который раньше жил только в сообщении Telegram: полная вакансия +
    разбор LLM (summary/matches/gaps/red_flags) + письмо + локальное состояние.
    """

    vacancy_id: str
    search_id: int | None = None            # какой поиск её нашёл
    name: str
    employer: Employer = Employer()
    salary_text: str = "не указана"
    area_name: str = ""
    url: str = ""
    published_at: datetime | None = None
    description: str = ""
    key_skills: list[str] = Field(default_factory=list)
    score: int = 0
    verdict: Verdict = Verdict.maybe
    summary: str = ""
    matches: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    letter: str = ""
    status: CardStatus = CardStatus.new
    favorite: bool = False
    created_at: datetime | None = None
    applied_at: datetime | None = None
    skipped_at: datetime | None = None


class Event(BaseModel):
    """Системное уведомление пользователю (то, что раньше слал send_text в Telegram)."""

    id: int | None = None
    level: str = "info"
    text: str = ""
    created_at: datetime | None = None
