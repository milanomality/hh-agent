"""Тесты Storage на временной БД."""

from datetime import datetime, timezone

import aiosqlite
import pytest

from hh_agent.db.storage import Storage
from hh_agent.models import Application, ScoreResult, SearchQuery, Verdict


@pytest.fixture
async def storage(tmp_path):
    s = Storage(str(tmp_path / "test.db"))
    await s.init()
    yield s
    await s.close()


async def test_init_idempotent(tmp_path):
    s = Storage(str(tmp_path / "db.sqlite"))
    await s.init()
    await s.mark_seen("v1")
    await s.init()  # повторный init не роняет и не стирает данные
    assert await s.is_seen("v1")
    await s.close()


async def test_is_seen_mark_seen(storage):
    assert not await storage.is_seen("v1")
    score = ScoreResult(score=8, verdict=Verdict.apply, summary="хорошее совпадение")
    await storage.mark_seen("v1", score)
    assert await storage.is_seen("v1")
    await storage.mark_seen("v2")  # без оценки
    assert await storage.is_seen("v2")
    assert not await storage.is_seen("v3")


async def test_save_get_letter(storage):
    assert await storage.get_letter("v1") is None
    await storage.save_letter("v1", "Здравствуйте!")
    assert await storage.get_letter("v1") == "Здравствуйте!"
    await storage.save_letter("v1", "Обновлённое письмо")
    assert await storage.get_letter("v1") == "Обновлённое письмо"


async def test_searches_lifecycle(storage):
    q = await storage.add_search(
        SearchQuery(text="python разработчик", area="41", salary_from=100_000)
    )
    assert q.id is not None
    assert q.text == "python разработчик"

    active = await storage.list_searches()
    assert [s.id for s in active] == [q.id]
    assert active[0].salary_from == 100_000
    assert active[0].last_polled_at is None

    await storage.touch_search(q.id)  # без polled_at — текущее время UTC
    touched = (await storage.list_searches())[0]
    assert touched.last_polled_at is not None

    moment = datetime(2026, 7, 10, 12, 30, tzinfo=timezone.utc)
    await storage.touch_search(q.id, polled_at=moment)  # явный момент начала запроса
    touched = (await storage.list_searches())[0]
    assert touched.last_polled_at == moment

    await storage.deactivate_search(q.id)
    assert await storage.list_searches() == []
    everything = await storage.list_searches(only_active=False)
    assert len(everything) == 1
    assert everything[0].active is False


async def test_applications(storage):
    await storage.save_application(
        Application(vacancy_id="v1", resume_id="r1", letter="текст письма")
    )
    apps = await storage.list_applications()
    assert len(apps) == 1
    app = apps[0]
    assert app.vacancy_id == "v1"
    assert app.resume_id == "r1"
    assert app.letter == "текст письма"
    assert app.state == "manual"  # дефолт модели: отклик отмечен человеком
    assert app.created_at is not None  # проставлено хранилищем


async def test_set_favorite(tmp_path):
    path = str(tmp_path / "fav.db")
    s = Storage(path)
    await s.init()
    await s.mark_seen("v1", ScoreResult(score=5, verdict=Verdict.maybe, summary="."))
    await s.set_favorite("v1")
    await s.set_favorite("v2")  # ещё не виденная — создаётся запись

    async with aiosqlite.connect(path) as db:
        rows = dict(await db.execute_fetchall("SELECT vacancy_id, favorite FROM seen"))
    assert rows == {"v1": 1, "v2": 1}

    await s.set_favorite("v1", False)
    async with aiosqlite.connect(path) as db:
        rows = dict(await db.execute_fetchall("SELECT vacancy_id, favorite FROM seen"))
        scores = dict(await db.execute_fetchall("SELECT vacancy_id, score FROM seen"))
    assert rows["v1"] == 0
    assert scores["v1"] == 5  # set_favorite не трогает оценку
    await s.close()
