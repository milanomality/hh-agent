"""Тесты персиста карточек и системных событий (веб-фид)."""

import asyncio
from datetime import datetime, timezone

import pytest

from hh_agent.db.storage import Storage
from hh_agent.models import CardStatus, Employer, ScoreResult, Vacancy, Verdict


@pytest.fixture
async def storage(tmp_path):
    s = Storage(str(tmp_path / "cards.db"))
    await s.init()
    yield s
    await s.close()


def vac(vid: str = "v1", **kw) -> Vacancy:
    defaults = dict(
        id=vid,
        name=f"Вакансия {vid}",
        employer=Employer(id="e1", name="ООО Ромашка"),
        salary_text="от 100 000 ₽",
        area_name="Москва",
        url=f"https://hh.ru/vacancy/{vid}",
        published_at=datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
        description="Описание вакансии",
        key_skills=["Python", "SQL"],
    )
    defaults.update(kw)
    return Vacancy(**defaults)


def score(sc: int = 8, **kw) -> ScoreResult:
    defaults = dict(
        score=sc,
        verdict=Verdict.apply,
        summary="ок",
        matches=["m1", "m2"],
        gaps=["g1"],
        red_flags=["r1"],
    )
    defaults.update(kw)
    return ScoreResult(**defaults)


async def test_save_get_card_roundtrip(storage):
    await storage.save_card(vac("v1"), score(8), "письмо", search_id=3)
    card = await storage.get_card("v1")

    assert card is not None
    assert card.vacancy_id == "v1"
    assert card.search_id == 3
    assert card.name == "Вакансия v1"
    assert card.employer.id == "e1"
    assert card.employer.name == "ООО Ромашка"
    assert card.salary_text == "от 100 000 ₽"
    assert card.area_name == "Москва"
    assert card.url == "https://hh.ru/vacancy/v1"
    assert card.published_at == datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    assert card.description == "Описание вакансии"
    assert card.key_skills == ["Python", "SQL"]  # JSON round-trip
    assert card.score == 8
    assert card.verdict == Verdict.apply
    assert card.summary == "ок"
    assert card.matches == ["m1", "m2"]
    assert card.gaps == ["g1"]
    assert card.red_flags == ["r1"]
    assert card.letter == "письмо"
    assert card.status == CardStatus.new
    assert card.favorite is False
    assert card.created_at is not None
    assert card.applied_at is None
    assert card.skipped_at is None


async def test_get_card_missing_returns_none(storage):
    assert await storage.get_card("nope") is None


async def test_list_cards_filters(storage):
    await storage.save_card(vac("v1"), score(9), "l1", search_id=1)
    await storage.save_card(vac("v2"), score(5), "l2", search_id=2)
    await storage.save_card(vac("v3"), score(7), "l3", search_id=1)
    await storage.set_card_favorite("v2")
    await storage.mark_card_applied("v3")

    assert {c.vacancy_id for c in await storage.list_cards(min_score=7)} == {"v1", "v3"}
    assert {c.vacancy_id for c in await storage.list_cards(favorite=True)} == {"v2"}
    assert {c.vacancy_id for c in await storage.list_cards(status=CardStatus.new)} == {"v1", "v2"}
    assert {c.vacancy_id for c in await storage.list_cards(status="applied")} == {"v3"}
    assert {c.vacancy_id for c in await storage.list_cards(search_id=1)} == {"v1", "v3"}
    # комбинация фильтров
    combined = await storage.list_cards(min_score=7, search_id=1, status=CardStatus.new)
    assert {c.vacancy_id for c in combined} == {"v1"}


async def test_count_cards(storage):
    await storage.save_card(vac("v1"), score(9), "l", search_id=1)
    await storage.save_card(vac("v2"), score(4), "l", search_id=1)
    assert await storage.count_cards() == 2
    assert await storage.count_cards(min_score=7) == 1
    assert await storage.count_cards(search_id=1) == 2
    assert await storage.count_cards(search_id=99) == 0


async def test_list_cards_pagination_and_order(storage):
    await storage.save_card(vac("v1"), score(8), "l")
    await asyncio.sleep(0.01)  # гарантируем различимые created_at (порядок DESC)
    await storage.save_card(vac("v2"), score(8), "l")
    await asyncio.sleep(0.01)
    await storage.save_card(vac("v3"), score(8), "l")

    assert [c.vacancy_id for c in await storage.list_cards()] == ["v3", "v2", "v1"]
    page1 = await storage.list_cards(limit=2, offset=0)
    page2 = await storage.list_cards(limit=2, offset=2)
    assert [c.vacancy_id for c in page1] == ["v3", "v2"]
    assert [c.vacancy_id for c in page2] == ["v1"]


async def test_mark_applied_sets_status_timestamp_and_keeps_favorite(storage):
    await storage.save_card(vac("v1"), score(8), "l")
    await storage.set_card_favorite("v1")
    await storage.mark_card_applied("v1")

    card = await storage.get_card("v1")
    assert card.status == CardStatus.applied
    assert card.applied_at is not None
    assert card.favorite is True  # действие не трогает favorite


async def test_mark_skipped_sets_status_and_timestamp(storage):
    await storage.save_card(vac("v1"), score(8), "l")
    await storage.mark_card_skipped("v1")

    card = await storage.get_card("v1")
    assert card.status == CardStatus.skipped
    assert card.skipped_at is not None


async def test_set_card_favorite_independent_of_status(storage):
    await storage.save_card(vac("v1"), score(8), "l")
    await storage.mark_card_applied("v1")

    await storage.set_card_favorite("v1", True)
    card = await storage.get_card("v1")
    assert card.favorite is True
    assert card.status == CardStatus.applied  # favorite не сбросил статус

    await storage.set_card_favorite("v1", False)
    assert (await storage.get_card("v1")).favorite is False


async def test_save_card_resend_preserves_user_state(storage):
    await storage.save_card(vac("v1", name="Старое имя"), score(6, summary="старый"), "старое", search_id=1)
    await storage.mark_card_applied("v1")
    await storage.set_card_favorite("v1")
    created_before = (await storage.get_card("v1")).created_at

    # повторная отправка обновляет только контент
    await storage.save_card(vac("v1", name="Новое имя"), score(9, summary="новый"), "новое", search_id=2)

    card = await storage.get_card("v1")
    assert card.name == "Новое имя"
    assert card.score == 9
    assert card.summary == "новый"
    assert card.letter == "новое"
    assert card.search_id == 2
    # действия пользователя и created_at сохранены
    assert card.status == CardStatus.applied
    assert card.favorite is True
    assert card.created_at == created_before


async def test_events_roundtrip_order_and_pagination(storage):
    await storage.add_event("первое")
    await storage.add_event("второе", level="warning")

    events = await storage.list_events()
    assert [e.text for e in events] == ["второе", "первое"]  # новые первыми (id DESC)
    assert events[0].level == "warning"
    assert events[1].level == "info"
    assert events[0].id is not None
    assert events[0].created_at is not None

    assert [e.text for e in await storage.list_events(limit=1)] == ["второе"]
    assert [e.text for e in await storage.list_events(limit=1, offset=1)] == ["первое"]
