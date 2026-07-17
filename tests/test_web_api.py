"""Тесты REST API веб-интерфейса: httpx.ASGITransport поверх приложения.

Приложение поднимается с инъектированным пресид-storage и start_scheduler=False —
без сети и APScheduler; реальный lifespan прогоняется через lifespan_context."""

import httpx
import pytest

from hh_agent.config import Settings
from hh_agent.db.storage import Storage
from hh_agent.models import Employer, ScoreResult, Vacancy, Verdict
from hh_agent.web.app import create_app


def vac(vid: str) -> Vacancy:
    return Vacancy(
        id=vid,
        name=f"Вакансия {vid}",
        employer=Employer(id="e1", name="ООО Ромашка"),
        salary_text="от 100 000 ₽",
        area_name="Москва",
        url=f"https://hh.ru/vacancy/{vid}",
        key_skills=["Python"],
    )


def score(sc: int) -> ScoreResult:
    return ScoreResult(score=sc, verdict=Verdict.apply, summary="ок", matches=["m"], gaps=[], red_flags=[])


@pytest.fixture
async def ctx(tmp_path):
    settings = Settings(
        _env_file=None,
        web_password="pw",
        web_secret="secret-key-for-tests",
        web_secure_cookie=False,  # кука без Secure → работает по http://test
        db_path=str(tmp_path / "api.db"),
        llm_providers_file=str(tmp_path / "absent.json"),
    )
    storage = Storage(settings.db_path)
    await storage.init()
    await storage.save_card(vac("a"), score(9), "letter-a", search_id=1)
    await storage.save_card(vac("b"), score(4), "letter-b", search_id=1)
    await storage.save_card(vac("c"), score(7), "letter-c", search_id=2)

    app = create_app(settings=settings, storage=storage, start_scheduler=False)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, storage
    await storage.close()


async def _login(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/login", json={"password": "pw"})
    assert r.status_code == 204


# ── авторизация ──────────────────────────────────────────────────────────────


async def test_healthz_is_public(ctx):
    client, _ = ctx
    r = await client.get("/api/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"


async def test_cards_requires_auth(ctx):
    client, _ = ctx
    assert (await client.get("/api/cards")).status_code == 401


async def test_login_wrong_password_stays_locked(ctx):
    client, _ = ctx
    assert (await client.post("/api/login", json={"password": "nope"})).status_code == 401
    assert (await client.get("/api/cards")).status_code == 401


async def test_session_and_logout(ctx):
    client, _ = ctx
    assert (await client.get("/api/session")).json() == {"authenticated": False}
    await _login(client)
    assert (await client.get("/api/session")).json() == {"authenticated": True}
    assert (await client.post("/api/logout")).status_code == 204
    assert (await client.get("/api/session")).json() == {"authenticated": False}


# ── карточки ─────────────────────────────────────────────────────────────────


async def test_cards_list_and_filters(ctx):
    client, _ = ctx
    await _login(client)

    body = (await client.get("/api/cards")).json()
    assert body["total"] == 3
    assert {c["vacancy_id"] for c in body["items"]} == {"a", "b", "c"}

    r = await client.get("/api/cards", params={"min_score": 7})
    assert {c["vacancy_id"] for c in r.json()["items"]} == {"a", "c"}
    assert r.json()["total"] == 2

    r = await client.get("/api/cards", params={"search_id": 1})
    assert {c["vacancy_id"] for c in r.json()["items"]} == {"a", "b"}

    assert (await client.get("/api/cards", params={"status": "new"})).json()["total"] == 3
    assert (await client.get("/api/cards", params={"status": "bogus"})).status_code == 422


async def test_cards_pagination(ctx):
    client, _ = ctx
    await _login(client)
    page1 = (await client.get("/api/cards", params={"limit": 2, "offset": 0})).json()
    page2 = (await client.get("/api/cards", params={"limit": 2, "offset": 2})).json()
    assert len(page1["items"]) == 2 and page1["total"] == 3
    assert len(page2["items"]) == 1


async def test_get_card_and_404(ctx):
    client, _ = ctx
    await _login(client)
    assert (await client.get("/api/cards/a")).json()["letter"] == "letter-a"
    assert (await client.get("/api/cards/zzz")).status_code == 404


async def test_applied_updates_card_and_funnel(ctx):
    client, storage = ctx
    await _login(client)

    r = await client.post("/api/cards/a/applied")
    assert r.status_code == 200 and r.json()["status"] == "applied"

    funnel = (await client.get("/api/funnel")).json()
    assert funnel["applications_total"] == 1
    assert funnel["by_state"]["manual"] == 1
    assert funnel["cards_by_status"] == {"new": 2, "applied": 1, "skipped": 0}

    apps = await storage.list_applications()
    assert apps[0].vacancy_id == "a" and apps[0].letter == "letter-a"


async def test_skip_and_favorite(ctx):
    client, _ = ctx
    await _login(client)

    assert (await client.post("/api/cards/b/skip")).json()["status"] == "skipped"

    r = await client.post("/api/cards/b/favorite", json={"favorite": True})
    assert r.json()["favorite"] is True and r.json()["status"] == "skipped"  # favorite не трогает статус
    r = await client.post("/api/cards/b/favorite", json={"favorite": False})
    assert r.json()["favorite"] is False

    await client.post("/api/cards/a/favorite", json={"favorite": True})
    r = await client.get("/api/cards", params={"favorite": "true"})
    assert {c["vacancy_id"] for c in r.json()["items"]} == {"a"}


async def test_actions_on_missing_card_404(ctx):
    client, _ = ctx
    await _login(client)
    assert (await client.post("/api/cards/zzz/applied")).status_code == 404
    assert (await client.post("/api/cards/zzz/skip")).status_code == 404
    assert (await client.post("/api/cards/zzz/favorite", json={"favorite": True})).status_code == 404


# ── поиски / события / статус ────────────────────────────────────────────────


async def test_searches_crud(ctx):
    client, _ = ctx
    await _login(client)

    r = await client.post("/api/searches", json={"text": "python разработчик", "area": "1"})
    assert r.status_code == 201
    sid = r.json()["id"]
    assert r.json()["text"] == "python разработчик"

    assert [s["id"] for s in (await client.get("/api/searches")).json()] == [sid]

    assert (await client.post(f"/api/searches/{sid}/deactivate")).status_code == 204
    assert (await client.get("/api/searches")).json() == []
    everything = (await client.get("/api/searches", params={"include_inactive": "true"})).json()
    assert [s["id"] for s in everything] == [sid]
    assert everything[0]["active"] is False


async def test_add_search_rejects_empty_text(ctx):
    client, _ = ctx
    await _login(client)
    assert (await client.post("/api/searches", json={"text": ""})).status_code == 422


async def test_events(ctx):
    client, storage = ctx
    await _login(client)
    await storage.add_event("тест-событие", level="warning")
    events = (await client.get("/api/events")).json()
    assert events[0]["text"] == "тест-событие"
    assert events[0]["level"] == "warning"


async def test_status_shape(ctx):
    client, _ = ctx
    await _login(client)
    body = (await client.get("/api/status")).json()
    assert set(body) == {"last_poll_at", "pass_failed", "hh_token_present", "hh_creds_present"}
    assert body["hh_creds_present"] is False  # в тестовых настройках кредов hh нет
    assert body["hh_token_present"] is False
    assert body["last_poll_at"] is None


async def test_applied_is_idempotent(ctx):
    client, storage = ctx
    await _login(client)
    await client.post("/api/cards/a/applied")
    first_applied_at = (await client.get("/api/cards/a")).json()["applied_at"]

    # повторный POST не плодит записи в воронке и не сдвигает applied_at
    await client.post("/api/cards/a/applied")

    funnel = (await client.get("/api/funnel")).json()
    assert funnel["applications_total"] == 1
    assert funnel["cards_by_status"]["applied"] == 1
    assert (await client.get("/api/cards/a")).json()["applied_at"] == first_applied_at
    assert len(await storage.list_applications()) == 1


async def test_login_accepts_non_ascii_password(tmp_path):
    """Кириллический web_password не должен ронять вход в 500 (compare_digest по байтам)."""
    settings = Settings(
        _env_file=None,
        web_password="пароль-Ключ",
        web_secret="secret-key-for-tests",
        web_secure_cookie=False,
        db_path=str(tmp_path / "nonascii.db"),
        llm_providers_file=str(tmp_path / "absent.json"),
    )
    storage = Storage(settings.db_path)
    await storage.init()
    app = create_app(settings=settings, storage=storage, start_scheduler=False)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.post("/api/login", json={"password": "пароль-Ключ"})).status_code == 204
            assert (await client.get("/api/cards")).status_code == 200
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as other:
            # неверный не-ASCII пароль → 401, не 500
            assert (await other.post("/api/login", json={"password": "неверный"})).status_code == 401
    await storage.close()
