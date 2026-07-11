"""Тесты рендера карточки вакансии и inline-клавиатуры (без Telegram API)."""

from hh_agent.bot.cards import render_vacancy_card, vacancy_keyboard
from hh_agent.models import Employer, ScoreResult, Vacancy, Verdict


def make_vacancy(**kwargs) -> Vacancy:
    defaults = dict(
        id="123",
        name="Python-разработчик",
        employer=Employer(id="7", name="Рога и Копыта"),
        salary_text="от 100 000 ₽",
        area_name="Калининград",
        url="https://hh.ru/vacancy/123",
    )
    defaults.update(kwargs)
    return Vacancy(**defaults)


def make_score(**kwargs) -> ScoreResult:
    defaults = dict(
        score=8,
        verdict=Verdict.apply,
        summary="Хорошее совпадение по стеку.",
        matches=["Python", "asyncio"],
        gaps=["Kubernetes"],
        red_flags=["Зарплата не указана"],
    )
    defaults.update(kwargs)
    return ScoreResult(**defaults)


def test_card_contains_key_fields():
    card = render_vacancy_card(make_vacancy(), make_score(), "Здравствуйте! Хочу к вам.")
    assert "Python-разработчик" in card
    assert 'href="https://hh.ru/vacancy/123"' in card
    assert "Рога и Копыта" in card
    assert "от 100 000 ₽" in card
    assert "Калининград" in card
    assert "8/10" in card
    assert "Хорошее совпадение по стеку." in card
    assert "✅ Python" in card
    assert "⚠️ Kubernetes" in card
    assert "🚩 Зарплата не указана" in card
    assert "<blockquote>Здравствуйте! Хочу к вам.</blockquote>" in card


def test_card_escapes_html_in_user_strings():
    vacancy = make_vacancy(name='<b>Разработчик</b> & "junior"')
    card = render_vacancy_card(vacancy, make_score(), "Письмо с <tag> & амперсандом")
    assert "<b>Разработчик</b>" not in card
    assert "&lt;b&gt;Разработчик&lt;/b&gt; &amp; &quot;junior&quot;" in card
    assert "Письмо с &lt;tag&gt; &amp; амперсандом" in card


def test_card_without_letter_and_empty_lists():
    score = make_score(matches=[], gaps=[], red_flags=[])
    card = render_vacancy_card(make_vacancy(), score, None)
    assert "<blockquote>" not in card
    assert "✅" not in card
    assert "⚠️" not in card
    assert "🚩" not in card


def test_card_without_url_has_no_link():
    card = render_vacancy_card(make_vacancy(url=""), make_score(), None)
    assert "<a href" not in card
    assert "<b>Python-разработчик</b>" in card


def test_keyboard_url_row_and_fixed_callback_data():
    kb = vacancy_keyboard(make_vacancy())
    assert len(kb.inline_keyboard) == 2

    (url_button,) = kb.inline_keyboard[0]
    assert url_button.text == "🔗 Открыть на hh.ru"
    assert url_button.url == "https://hh.ru/vacancy/123"
    assert url_button.callback_data is None

    actions = kb.inline_keyboard[1]
    assert [b.callback_data for b in actions] == ["applied:123", "skip:123", "fav:123"]
    assert [b.text for b in actions] == ["✅ Откликнулся", "⏭ Пропустить", "⭐ В избранное"]


def test_keyboard_without_url_has_no_url_row():
    kb = vacancy_keyboard(make_vacancy(id="42", url=""))
    assert len(kb.inline_keyboard) == 1
    data = [b.callback_data for b in kb.inline_keyboard[0]]
    assert data == ["applied:42", "skip:42", "fav:42"]
    assert all(b.url is None for b in kb.inline_keyboard[0])
