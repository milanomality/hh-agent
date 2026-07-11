"""Тесты HHClient: маппинг поиска, HTML-очистка, заголовок, ретраи, капча (respx)."""

from __future__ import annotations

import httpx
import pytest
import respx
from httpx import Response

from hh_agent.config import Settings
from hh_agent.hh.client import HHApiError, HHClient
from hh_agent.models import SearchQuery

BASE = "https://api.hh.ru"

SEARCH_RESPONSE = {
    "found": 2,
    "pages": 1,
    "page": 0,
    "per_page": 100,
    "items": [
        {
            "id": "101",
            "name": "Аналитик данных",
            "employer": {"id": "77", "name": "ООО Рога"},
            "salary": {"from": 60000, "to": 90000, "currency": "RUR", "gross": False},
            "area": {"id": "41", "name": "Калининград"},
            "alternate_url": "https://hh.ru/vacancy/101",
            "published_at": "2026-07-10T12:00:00+0300",
        },
        {
            "id": "102",
            "name": "Junior Python",
            "employer": {"name": "Копыта"},
            "salary": None,
            "area": {"id": "1", "name": "Москва"},
            "alternate_url": "https://hh.ru/vacancy/102",
            "published_at": "2026-07-10T13:00:00+0300",
        },
    ],
}

VACANCY_DETAIL = {
    "id": "101",
    "name": "Аналитик данных",
    "employer": {"id": "77", "name": "ООО Рога"},
    "salary": {"from": 60000, "to": None, "currency": "RUR", "gross": True},
    "area": {"id": "41", "name": "Калининград"},
    "alternate_url": "https://hh.ru/vacancy/101",
    "published_at": "2026-07-10T12:00:00+0300",
    "description": (
        "<p>Ищем аналитика.</p>"
        "<ul><li>SQL &amp; Python</li><li>BI-системы</li></ul>"
        "<p>Удалёнка<br>гибкий график</p>"
    ),
    "key_skills": [{"name": "SQL"}, {"name": "Python"}],
}


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, hh_user_agent="hh-agent-test/1.0 (test@example.com)")


@pytest.fixture
async def client(settings):
    hh = HHClient(settings)
    hh.retry_delay = 0  # не ждать backoff в тестах
    yield hh
    await hh.close()


@respx.mock
async def test_search_vacancies_mapping(client):
    route = respx.get(f"{BASE}/vacancies").mock(return_value=Response(200, json=SEARCH_RESPONSE))
    query = SearchQuery(text="python", area="41", salary_from=60000, schedule="remote")
    vacancies = await client.search_vacancies(query, date_from="2026-07-10T00:00:00")

    assert route.call_count == 1  # pages=1 — вторую страницу не запрашиваем
    params = route.calls.last.request.url.params
    assert params["text"] == "python"
    assert params["area"] == "41"
    assert params["salary"] == "60000"
    assert params["schedule"] == "remote"
    assert params["per_page"] == "100"
    assert params["date_from"] == "2026-07-10T00:00:00"
    assert "only_with_salary" not in params

    first, second = vacancies
    assert first.id == "101"
    assert first.name == "Аналитик данных"
    assert first.employer.id == "77"
    assert first.employer.name == "ООО Рога"
    assert first.salary_text == "60 000 – 90 000 ₽ на руки"
    assert first.area_name == "Калининград"
    assert first.url == "https://hh.ru/vacancy/101"
    assert first.published_at is not None

    assert second.salary_text == "не указана"
    assert second.employer.id is None


@respx.mock
async def test_search_pagination_capped_at_three_pages(client):
    def page_response(request):
        page = int(request.url.params["page"])
        return Response(
            200, json={"pages": 10, "page": page, "items": [{"id": str(page), "name": f"v{page}"}]}
        )

    route = respx.get(f"{BASE}/vacancies").mock(side_effect=page_response)
    vacancies = await client.search_vacancies(SearchQuery(text="x"))

    assert route.call_count == 3
    assert [v.id for v in vacancies] == ["0", "1", "2"]


@respx.mock
async def test_get_vacancy_strips_html(client):
    respx.get(f"{BASE}/vacancies/101").mock(return_value=Response(200, json=VACANCY_DETAIL))
    vacancy = await client.get_vacancy("101")

    assert "<" not in vacancy.description and ">" not in vacancy.description
    assert "Ищем аналитика." in vacancy.description
    assert "SQL & Python" in vacancy.description  # HTML-сущности расшифрованы
    assert "Удалёнка\nгибкий график" in vacancy.description  # <br> → перенос строки
    assert vacancy.description.count("\n") >= 3  # <p>/<li> дали переносы
    assert vacancy.key_skills == ["SQL", "Python"]
    assert vacancy.salary_text == "от 60 000 ₽ до вычета налогов"


@respx.mock
async def test_hh_user_agent_header_on_every_request(client, settings):
    respx.get(f"{BASE}/vacancies/1").mock(return_value=Response(200, json={"id": "1", "name": "x"}))
    await client.get_vacancy("1")

    request = respx.calls.last.request
    assert request.headers["HH-User-Agent"] == settings.hh_user_agent


@respx.mock
async def test_retry_on_429_then_success(client):
    route = respx.get(f"{BASE}/vacancies/5").mock(
        side_effect=[
            Response(429),
            Response(429),
            Response(200, json={"id": "5", "name": "ok"}),
        ]
    )
    vacancy = await client.get_vacancy("5")

    assert route.call_count == 3
    assert vacancy.name == "ok"


@respx.mock
async def test_retry_on_transport_error(client):
    route = respx.get(f"{BASE}/vacancies/8").mock(
        side_effect=[httpx.ConnectError("boom"), Response(200, json={"id": "8", "name": "ok"})]
    )
    vacancy = await client.get_vacancy("8")

    assert route.call_count == 2
    assert vacancy.name == "ok"


@respx.mock
async def test_retry_exhausted_raises_hh_api_error(client):
    respx.get(f"{BASE}/vacancies/6").mock(return_value=Response(429))
    with pytest.raises(HHApiError) as err:
        await client.get_vacancy("6")
    assert err.value.status == 429


@respx.mock
async def test_captcha_required_gives_readable_error(client):
    respx.get(f"{BASE}/vacancies/7").mock(
        return_value=Response(403, json={"errors": [{"type": "captcha_required"}]})
    )
    with pytest.raises(HHApiError) as err:
        await client.get_vacancy("7")
    assert err.value.status == 403
    assert "капчу" in str(err.value)


# ── авторизация приложения (client_credentials) ─────────────────────────────


@pytest.fixture
async def auth_client():
    hh = HHClient(
        Settings(
            _env_file=None,
            hh_user_agent="hh-agent-test/1.0 (test@example.com)",
            hh_client_id="cid",
            hh_client_secret="csecret",
        )
    )
    hh.retry_delay = 0
    yield hh
    await hh.close()


@respx.mock
async def test_app_token_requested_and_sent(auth_client):
    """При наличии client_id/secret клиент получает app-токен и шлёт Bearer."""
    token_route = respx.post("https://api.hh.ru/token").mock(
        return_value=httpx.Response(200, json={"access_token": "app-tok"})
    )
    search_route = respx.get("https://api.hh.ru/vacancies").mock(
        return_value=httpx.Response(200, json={"items": [], "pages": 1})
    )
    await auth_client.search_vacancies(SearchQuery(text="python"))
    assert token_route.called
    sent = search_route.calls[0].request
    assert sent.headers["Authorization"] == "Bearer app-tok"
    body = token_route.calls[0].request.content.decode()
    assert "grant_type=client_credentials" in body and "client_id=cid" in body


@respx.mock
async def test_app_token_reissued_on_403(auth_client):
    """403 с валидными кредами → один перевыпуск токена и повтор запроса."""
    tokens = iter(["old-tok", "new-tok"])
    respx.post("https://api.hh.ru/token").mock(
        side_effect=lambda request: httpx.Response(200, json={"access_token": next(tokens)})
    )
    search_route = respx.get("https://api.hh.ru/vacancies").mock(
        side_effect=[
            httpx.Response(403, json={"errors": [{"type": "forbidden"}]}),
            httpx.Response(200, json={"items": [], "pages": 1}),
        ]
    )
    await auth_client.search_vacancies(SearchQuery(text="python"))
    assert search_route.call_count == 2
    assert search_route.calls[1].request.headers["Authorization"] == "Bearer new-tok"


@respx.mock
async def test_anonymous_403_message_mentions_app_registration(client):
    """Без кредов 403 даёт подсказку про регистрацию приложения на dev.hh.ru."""
    respx.get("https://api.hh.ru/vacancies").mock(
        return_value=httpx.Response(403, json={"errors": [{"type": "forbidden"}]})
    )
    with pytest.raises(HHApiError) as err:
        await client.search_vacancies(SearchQuery(text="python"))
    assert "dev.hh.ru" in str(err.value) and err.value.status == 403


@respx.mock
async def test_app_token_error_is_wrapped(auth_client):
    """Ошибка выдачи токена (например, 400) — контрактный HHApiError."""
    respx.post("https://api.hh.ru/token").mock(
        return_value=httpx.Response(400, json={"error": "invalid_client"})
    )
    with pytest.raises(HHApiError) as err:
        await auth_client.search_vacancies(SearchQuery(text="python"))
    assert err.value.status == 400
