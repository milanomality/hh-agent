"""Тесты poll_once на фейках протоколов; резюме — из локального файла (tmp_path)."""

from datetime import datetime, timezone

from hh_agent.config import Settings
from hh_agent.models import ScoreResult, SearchQuery, Vacancy, Verdict
from hh_agent.scheduler import poll_once

RESUME_TEXT = "\nPython-разработчик\n\nТри года пишу асинхронный Python.\n"


def make_settings(tmp_path, resume: str | None = RESUME_TEXT, **kw) -> Settings:
    """Настройки с резюме-файлом в tmp_path; resume=None — файл не создаётся."""
    resume_file = tmp_path / "resume.md"
    if resume is not None:
        resume_file.write_text(resume, encoding="utf-8")
    return Settings(_env_file=None, resume_path=str(resume_file), **kw)


def vac(vid: str) -> Vacancy:
    return Vacancy(id=vid, name=f"Вакансия {vid}")


class FakeHH:
    def __init__(self, found=None, fail_vacancy_ids=(), delete_on_search=None):
        self.found = found or []
        self.fail_vacancy_ids = set(fail_vacancy_ids)
        self.delete_on_search = delete_on_search  # Path: удалить при поиске (резюме-файл)
        self.get_vacancy_calls: list[str] = []
        self.search_calls = 0
        self.last_date_from: str | None = None
        self.search_called_at: datetime | None = None  # момент вызова search_vacancies

    async def search_vacancies(self, query, *, date_from=None):
        if self.delete_on_search is not None:
            self.delete_on_search.unlink(missing_ok=True)
        self.search_calls += 1
        self.last_date_from = date_from
        self.search_called_at = datetime.now(timezone.utc)
        return self.found

    async def get_vacancy(self, vacancy_id):
        self.get_vacancy_calls.append(vacancy_id)
        if vacancy_id in self.fail_vacancy_ids:
            raise RuntimeError("hh недоступен")
        return next(v for v in self.found if v.id == vacancy_id)


class FakeScorer:
    def __init__(self, scores: dict[str, int] | None = None):
        self.scores = scores or {}
        self.scored_with: list[tuple[str, str]] = []  # (vacancy_id, resume_id)
        self.resumes_seen: list = []
        self.letter_calls: list[str] = []

    async def score_vacancy(self, vacancy, resume):
        self.scored_with.append((vacancy.id, resume.id))
        self.resumes_seen.append(resume)
        return ScoreResult(score=self.scores[vacancy.id], verdict=Verdict.apply, summary="ок")

    async def write_cover_letter(self, vacancy, resume, score):
        self.letter_calls.append(vacancy.id)
        return f"письмо для {vacancy.id}"


class FakeStorage:
    def __init__(self, searches=None, seen=None):
        self.searches = searches if searches is not None else [SearchQuery(id=1, text="python")]
        self.seen: dict[str, int | None] = dict.fromkeys(seen or [])
        self.letters: dict[str, str] = {}
        self.touched: list[tuple[int, datetime | None]] = []  # (search_id, polled_at)

    async def init(self):
        pass

    async def is_seen(self, vacancy_id):
        return vacancy_id in self.seen

    async def mark_seen(self, vacancy_id, score=None):
        self.seen[vacancy_id] = score.score if score else None

    async def set_favorite(self, vacancy_id, fav=True):
        pass

    async def save_letter(self, vacancy_id, letter):
        self.letters[vacancy_id] = letter

    async def get_letter(self, vacancy_id):
        return self.letters.get(vacancy_id)

    async def list_searches(self, only_active=True):
        return self.searches

    async def add_search(self, query):
        return query

    async def deactivate_search(self, search_id):
        pass

    async def touch_search(self, search_id, polled_at=None):
        self.touched.append((search_id, polled_at))

    async def save_application(self, app):
        pass

    async def list_applications(self):
        return []


class FakeNotifier:
    def __init__(self):
        self.cards: list[tuple[str, int, str | None]] = []
        self.card_search_ids: list[int | None] = []
        self.texts: list[str] = []

    async def send_vacancy_card(self, vacancy, score, letter, *, search_id=None):
        self.cards.append((vacancy.id, score.score, letter))
        self.card_search_ids.append(search_id)

    async def send_text(self, text):
        self.texts.append(text)


async def test_new_vacancy_scored_and_notified(tmp_path):
    hh = FakeHH(found=[vac("1")])
    scorer = FakeScorer({"1": 9})
    storage = FakeStorage()
    notifier = FakeNotifier()

    await poll_once(hh, scorer, storage, notifier, make_settings(tmp_path, score_threshold=7))

    assert storage.seen == {"1": 9}
    assert storage.letters == {"1": "письмо для 1"}
    assert notifier.cards == [("1", 9, "письмо для 1")]
    assert notifier.card_search_ids == [1]  # search.id проброшен в карточку
    assert [sid for sid, _ in storage.touched] == [1]


async def test_seen_vacancy_skipped(tmp_path):
    hh = FakeHH(found=[vac("1")])
    storage = FakeStorage(seen=["1"])
    notifier = FakeNotifier()

    await poll_once(hh, FakeScorer(), storage, notifier, make_settings(tmp_path))

    assert hh.get_vacancy_calls == []  # get_vacancy не вызывается
    assert notifier.cards == []
    assert [sid for sid, _ in storage.touched] == [1]  # поиск всё равно touch-ится


async def test_below_threshold_marked_but_not_notified(tmp_path):
    hh = FakeHH(found=[vac("1")])
    scorer = FakeScorer({"1": 3})
    storage = FakeStorage()
    notifier = FakeNotifier()

    await poll_once(hh, scorer, storage, notifier, make_settings(tmp_path, score_threshold=7))

    assert storage.seen == {"1": 3}  # mark_seen — всегда
    assert scorer.letter_calls == []
    assert storage.letters == {}
    assert notifier.cards == []


async def test_missing_resume_file_sends_hint_and_exits_early(tmp_path):
    hh = FakeHH(found=[vac("1")])
    storage = FakeStorage()
    notifier = FakeNotifier()
    settings = make_settings(tmp_path, resume=None)  # файла нет

    await poll_once(hh, FakeScorer(), storage, notifier, settings)

    assert len(notifier.texts) == 1
    hint = notifier.texts[0]
    assert settings.resume_path in hint
    assert "Создайте" in hint
    assert all(ch not in hint for ch in "<>&")  # уходит с parse_mode=HTML
    assert hh.search_calls == 0
    assert storage.touched == []


async def test_empty_resume_file_sends_hint_and_exits_early(tmp_path):
    hh = FakeHH(found=[vac("1")])
    storage = FakeStorage()
    notifier = FakeNotifier()

    await poll_once(hh, FakeScorer(), storage, notifier, make_settings(tmp_path, resume="  \n\n"))

    assert len(notifier.texts) == 1
    assert hh.search_calls == 0
    assert storage.touched == []


async def test_resume_parsed_from_file(tmp_path):
    long_line = "Python-разработчик " * 10  # первая непустая строка длиннее 80 символов
    resume_text = f"\n\n{long_line}\nОпыт: три года.\n"
    hh = FakeHH(found=[vac("1")])
    scorer = FakeScorer({"1": 9})

    await poll_once(
        hh, scorer, FakeStorage(), FakeNotifier(),
        make_settings(tmp_path, resume=resume_text, score_threshold=7),
    )

    (resume,) = scorer.resumes_seen
    assert resume.id == "local"
    assert resume.title == long_line.strip()[:80]  # первая непустая строка, обрезана до 80
    assert resume.text == resume_text  # текст — файл целиком


async def test_resume_read_once_before_searches(tmp_path):
    settings = make_settings(tmp_path)
    searches = [SearchQuery(id=1, text="a"), SearchQuery(id=2, text="b")]
    hh = FakeHH(delete_on_search=tmp_path / "resume.md")  # файл исчезает при первом поиске
    notifier = FakeNotifier()

    await poll_once(hh, FakeScorer(), FakeStorage(searches=searches), notifier, settings)

    # оба поиска обработаны без подсказки — резюме прочитано заранее и один раз
    assert hh.search_calls == 2
    assert notifier.texts == []


async def test_vacancy_error_does_not_stop_others(tmp_path):
    hh = FakeHH(found=[vac("1"), vac("2")], fail_vacancy_ids={"1"})
    scorer = FakeScorer({"2": 8})
    storage = FakeStorage()
    notifier = FakeNotifier()

    await poll_once(hh, scorer, storage, notifier, make_settings(tmp_path, score_threshold=7))

    assert "1" not in storage.seen  # упавшая не помечена — попробуем в следующий проход
    assert storage.seen == {"2": 8}
    assert notifier.cards == [("2", 8, "письмо для 2")]
    assert [sid for sid, _ in storage.touched] == [1]


async def test_date_from_passed_from_last_polled_at(tmp_path):
    dt = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    storage = FakeStorage(searches=[SearchQuery(id=5, text="q", last_polled_at=dt)])
    hh = FakeHH()

    await poll_once(hh, FakeScorer(), storage, FakeNotifier(), make_settings(tmp_path))

    assert hh.last_date_from == dt.isoformat()
    assert [sid for sid, _ in storage.touched] == [5]


async def test_touch_search_gets_moment_before_search_call(tmp_path):
    """Окно сдвигается на момент НАЧАЛА запроса: polled_at зафиксирован до search_vacancies."""
    hh = FakeHH(found=[vac("1")])
    storage = FakeStorage()

    before = datetime.now(timezone.utc)
    await poll_once(hh, FakeScorer({"1": 9}), storage, FakeNotifier(), make_settings(tmp_path))

    ((sid, polled_at),) = storage.touched
    assert sid == 1
    assert polled_at is not None
    assert hh.search_called_at is not None
    assert before <= polled_at <= hh.search_called_at  # не позже вызова поиска


# ── уведомление «весь проход упал» (дедуп между проходами) ───────────────────

import pytest

import hh_agent.scheduler as scheduler_mod


@pytest.fixture(autouse=True)
def _reset_pass_flag():
    """Межпроходный флаг — модульный глобал; изолируем тесты друг от друга."""
    scheduler_mod._last_pass_failed = False
    yield
    scheduler_mod._last_pass_failed = False


class BrokenHH(FakeHH):
    async def search_vacancies(self, query, *, date_from=None):
        raise RuntimeError("hh <сломался> & упал")


async def test_all_searches_failed_notifies_once_with_escaping(tmp_path):
    notifier = FakeNotifier()
    await poll_once(BrokenHH(), FakeScorer(), FakeStorage(), notifier, make_settings(tmp_path))
    assert len(notifier.texts) == 1
    assert "&lt;сломался&gt;" in notifier.texts[0]
    assert "<сломался>" not in notifier.texts[0]


async def test_repeated_failure_deduped_then_recovery_notice(tmp_path):
    settings = make_settings(tmp_path)
    notifier = FakeNotifier()
    storage = FakeStorage()
    await poll_once(BrokenHH(), FakeScorer(), storage, notifier, settings)
    await poll_once(BrokenHH(), FakeScorer(), storage, notifier, settings)
    assert len(notifier.texts) == 1  # повторный сбой не дублируется
    await poll_once(FakeHH(found=[]), FakeScorer(), storage, notifier, settings)
    assert len(notifier.texts) == 2
    assert "снова работает" in notifier.texts[1]


async def test_no_searches_means_no_failure_notice(tmp_path):
    notifier = FakeNotifier()
    await poll_once(
        BrokenHH(), FakeScorer(), FakeStorage(searches=[]), notifier, make_settings(tmp_path)
    )
    assert notifier.texts == []
