"""Асинхронный клиент открытого API hh.ru (реализует HHClientProto).

Соискательский API закрыт с 15.12.2025 — клиент использует только
публичные методы поиска: GET /vacancies и GET /vacancies/{id}.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from html.parser import HTMLParser
from typing import Any

import httpx

from ..config import Settings
from ..models import Employer, SearchQuery, Vacancy

BASE_URL = "https://api.hh.ru"
PER_PAGE = 100
MAX_PAGES = 3
RETRIES = 3

_CURRENCY = {"RUR": "₽", "RUB": "₽", "USD": "$", "EUR": "€"}

_STATUS_TEXT = {
    400: "некорректный запрос",
    401: "требуется авторизация (соискательский API hh.ru закрыт)",
    403: "доступ запрещён",
    404: "не найдено",
    429: "превышен лимит запросов к hh.ru",
}


class HHApiError(Exception):
    """Ошибка API hh.ru."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class _HTMLTextExtractor(HTMLParser):
    """Извлекает текст из HTML, вставляя переносы строк для блочных тегов."""

    _break_before = {"p", "br", "li", "ul", "ol", "div"}
    _break_after = {"p", "li", "ul", "ol", "div"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._break_before:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._break_after:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(html: str) -> str:
    """HTML → плоский текст: теги убраны, блочные превращены в переносы строк."""
    parser = _HTMLTextExtractor()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _fmt_num(value: int | float) -> str:
    return f"{int(value):,}".replace(",", " ")


def format_salary(salary: dict[str, Any] | None) -> str:
    """Зарплата в человекочитаемую строку: «от 60 000 ₽», «не указана»."""
    if not salary:
        return "не указана"
    low, high = salary.get("from"), salary.get("to")
    if low is None and high is None:
        return "не указана"
    currency = _CURRENCY.get(salary.get("currency") or "", salary.get("currency") or "")
    if low is not None and high is not None:
        text = f"{_fmt_num(low)} – {_fmt_num(high)}"
    elif low is not None:
        text = f"от {_fmt_num(low)}"
    else:
        text = f"до {_fmt_num(high)}"
    if currency:
        text += f" {currency}"
    gross = salary.get("gross")
    if gross is False:
        text += " на руки"
    elif gross is True:
        text += " до вычета налогов"
    return text


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _map_vacancy(item: dict[str, Any]) -> Vacancy:
    employer = item.get("employer") or {}
    return Vacancy(
        id=str(item["id"]),
        name=item.get("name") or "",
        employer=Employer(
            id=str(employer["id"]) if employer.get("id") else None,
            name=employer.get("name") or "",
        ),
        salary_text=format_salary(item.get("salary")),
        area_name=(item.get("area") or {}).get("name") or "",
        url=item.get("alternate_url") or "",
        published_at=_parse_dt(item.get("published_at")),
    )


class HHClient:
    """Клиент API hh.ru: поиск и карточки вакансий с авторизацией приложения.

    hh требует app-токен (client_credentials) даже для поиска; соискательская
    OAuth-авторизация не используется (закрыта с 15.12.2025).
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # trust_env=False: hh.ru доступен из РФ напрямую, а через системный
        # VPN-прокси (зарубежный выход) соединение не устанавливается.
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"HH-User-Agent": settings.hh_user_agent},
            timeout=30.0,
            trust_env=False,
        )
        self.retry_delay = 1.0  # база экспоненциального backoff (в тестах — 0)
        self._app_token: str | None = None
        self._token_lock = asyncio.Lock()

    async def close(self) -> None:
        await self._http.aclose()

    @property
    def _has_app_creds(self) -> bool:
        return bool(self._settings.hh_client_id and self._settings.hh_client_secret)

    async def _ensure_app_token(self) -> str | None:
        """App-токен hh (grant_type=client_credentials); кэшируется на процесс."""
        if not self._has_app_creds or self._app_token:
            return self._app_token
        async with self._token_lock:
            if self._app_token:
                return self._app_token
            response = await self._http.post(
                "/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._settings.hh_client_id,
                    "client_secret": self._settings.hh_client_secret,
                },
            )
            if response.status_code != 200:
                raise HHApiError(
                    f"Не удалось получить app-токен hh.ru ({response.status_code}): "
                    f"{response.text[:200]}",
                    response.status_code,
                )
            self._app_token = response.json().get("access_token") or None
            if not self._app_token:
                raise HHApiError("hh.ru вернул ответ без app-токена")
            return self._app_token

    async def _request(
        self, method: str, path: str, *, _retry_auth: bool = True, **kwargs: Any
    ) -> httpx.Response:
        """Запрос с ретраями на 429/5xx/сеть; при 403 — один перевыпуск app-токена."""
        token = await self._ensure_app_token()
        if token:
            kwargs = {
                **kwargs,
                "headers": {**kwargs.get("headers", {}), "Authorization": f"Bearer {token}"},
            }
        response: httpx.Response | None = None
        last_error: httpx.TransportError | None = None
        for attempt in range(RETRIES):
            try:
                response = await self._http.request(method, path, **kwargs)
                last_error = None
            except httpx.TransportError as exc:  # сеть — тоже транзиентный сбой
                response, last_error = None, exc
            if response is not None and response.status_code != 429 and response.status_code < 500:
                break
            if attempt + 1 < RETRIES:
                await asyncio.sleep(self.retry_delay * 2**attempt)
        if response is None:
            raise HHApiError(f"hh.ru недоступен: {last_error}") from last_error
        if response.is_success:
            return response
        if (
            response.status_code == 403
            and self._has_app_creds
            and _retry_auth
            and "captcha_required" not in response.text
        ):
            # токен мог быть отозван — перевыпускаем один раз (капча токеном не лечится)
            self._app_token = None
            return await self._request(method, path, _retry_auth=False, **kwargs)
        raise self._error(response)

    def _error(self, response: httpx.Response) -> HHApiError:
        status = response.status_code
        body = response.text[:300]
        if status == 403 and "captcha_required" in body:
            return HHApiError(
                "hh.ru требует капчу: откройте hh.ru в браузере, пройдите проверку и повторите попытку",
                status,
            )
        if status == 403 and not self._has_app_creds:
            return HHApiError(
                "hh.ru отклонил анонимный запрос (403): поиск вакансий требует авторизацию "
                "приложения. Зарегистрируйте приложение на https://dev.hh.ru и заполните "
                "HH_CLIENT_ID и HH_CLIENT_SECRET в .env",
                status,
            )
        reason = _STATUS_TEXT.get(
            status, "сервер hh.ru недоступен" if status >= 500 else "неизвестная ошибка"
        )
        return HHApiError(f"Ошибка API hh.ru ({status}): {reason}. Ответ: {body}", status)

    async def search_vacancies(
        self, query: SearchQuery, *, date_from: str | None = None
    ) -> list[Vacancy]:
        """Поиск вакансий: до 3 страниц по 100 штук."""
        params: dict[str, Any] = {"text": query.text, "per_page": PER_PAGE}
        if query.area:
            params["area"] = query.area
        if query.salary_from:
            params["salary"] = query.salary_from  # only_with_salary сознательно не ставим
        if query.experience:
            params["experience"] = query.experience
        if query.schedule:
            params["schedule"] = query.schedule
        if date_from:
            params["date_from"] = date_from

        result: list[Vacancy] = []
        for page in range(MAX_PAGES):
            response = await self._request("GET", "/vacancies", params={**params, "page": page})
            data = response.json()
            result.extend(_map_vacancy(item) for item in data.get("items", []))
            if page + 1 >= int(data.get("pages", 1)):
                break
        return result

    async def get_vacancy(self, vacancy_id: str) -> Vacancy:
        """Полная карточка вакансии: описание без HTML, key_skills."""
        response = await self._request("GET", f"/vacancies/{vacancy_id}")
        data = response.json()
        vacancy = _map_vacancy(data)
        vacancy.description = html_to_text(data.get("description") or "")
        vacancy.key_skills = [s["name"] for s in data.get("key_skills") or [] if s.get("name")]
        return vacancy
