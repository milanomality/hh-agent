"""Тесты WebNotifier: карточки и системные события уходят в хранилище, не в Telegram."""

import pytest

from hh_agent.db.storage import Storage
from hh_agent.models import Employer, ScoreResult, Vacancy, Verdict
from hh_agent.web.notifier import WebNotifier


@pytest.fixture
async def storage(tmp_path):
    s = Storage(str(tmp_path / "wn.db"))
    await s.init()
    yield s
    await s.close()


async def test_send_vacancy_card_persists_card(storage):
    notifier = WebNotifier(storage)
    vacancy = Vacancy(
        id="v1", name="Python-разработчик", employer=Employer(name="ООО"), key_skills=["Python"]
    )
    sc = ScoreResult(
        score=9, verdict=Verdict.apply, summary="огонь", matches=["опыт"], gaps=[], red_flags=[]
    )

    await notifier.send_vacancy_card(vacancy, sc, "письмо", search_id=7)

    card = await storage.get_card("v1")
    assert card is not None
    assert card.name == "Python-разработчик"
    assert card.score == 9
    assert card.letter == "письмо"
    assert card.search_id == 7
    assert card.matches == ["опыт"]


async def test_send_text_unescapes_html_entities(storage):
    """poll_once пре-экранирует алерт под Telegram-HTML — веб хранит чистый текст."""
    notifier = WebNotifier(storage)
    await notifier.send_text("hh &lt;сломался&gt; &amp; упал")

    (event,) = await storage.list_events()
    assert event.text == "hh <сломался> & упал"
