"""Тесты failover-цепочки провайдеров и сборки цепочки в make_scorer.

Сеть не нужна: провайдеры — управляемые заглушки, часы инжектируются.
"""

from __future__ import annotations

import json
import logging

import pytest

from hh_agent.ai.fallback import FallbackScorer
from hh_agent.ai.openai_scorer import OpenAICompatScorer, ProviderUnavailableError
from hh_agent.ai.scorer import ScoringError
from hh_agent.config import Settings
from hh_agent.main import make_scorer
from hh_agent.models import Resume, ScoreResult, Vacancy, Verdict

SCORE = ScoreResult(score=8, verdict=Verdict.apply, summary="Подходит.")
VACANCY = Vacancy(id="1", name="Python-разработчик")
RESUME = Resume(id="r1", text="Python, asyncio.")


class FakeClock:
    """Управляемое монотонное время."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeScorer:
    """Заглушка провайдера: отдаёт заранее заданные результаты или исключения."""

    def __init__(self, name: str, outcomes: list[object]) -> None:
        self.name = name
        self.outcomes = list(outcomes)
        self.calls = 0

    def _next(self) -> object:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def score_vacancy(self, vacancy, resume):
        return self._next()

    async def write_cover_letter(self, vacancy, resume, score):
        return self._next()


def unavailable(cooldown: float) -> ProviderUnavailableError:
    return ProviderUnavailableError("превышен лимит", cooldown_seconds=cooldown)


# ---------- FallbackScorer ----------


async def test_first_success_second_not_called():
    first = FakeScorer("groq", [SCORE])
    second = FakeScorer("openrouter", [SCORE])
    fallback = FallbackScorer([first, second], clock=FakeClock())

    result = await fallback.score_vacancy(VACANCY, RESUME)

    assert result is SCORE
    assert first.calls == 1
    assert second.calls == 0


async def test_unavailable_switches_and_sets_cooldown(caplog):
    clock = FakeClock()
    first = FakeScorer("groq", [unavailable(60)])
    second = FakeScorer("openrouter", [SCORE, SCORE])
    fallback = FallbackScorer([first, second], clock=clock)

    with caplog.at_level(logging.WARNING, logger="hh_agent.ai.fallback"):
        assert await fallback.score_vacancy(VACANCY, RESUME) is SCORE
    assert first.calls == 1
    assert second.calls == 1
    # переключение залогировано с именем провайдера и причиной
    warning = next(r.getMessage() for r in caplog.records if "groq" in r.getMessage())
    assert "лимит" in warning

    # повторный вызов в течение cooldown — первый пропускается сразу
    clock.advance(30)
    assert await fallback.score_vacancy(VACANCY, RESUME) is SCORE
    assert first.calls == 1
    assert second.calls == 2


async def test_provider_returns_after_cooldown_expires():
    clock = FakeClock()
    first = FakeScorer("groq", [unavailable(60), SCORE])
    second = FakeScorer("openrouter", [SCORE])
    fallback = FallbackScorer([first, second], clock=clock)

    await fallback.score_vacancy(VACANCY, RESUME)  # groq в cooldown, ответил openrouter
    clock.advance(61)
    assert await fallback.score_vacancy(VACANCY, RESUME) is SCORE
    assert first.calls == 2  # остывший groq снова первый в цепочке
    assert second.calls == 1


async def test_plain_scoring_error_switches_without_cooldown():
    first = FakeScorer("groq", [ScoringError("кривой JSON"), SCORE])
    second = FakeScorer("openrouter", [SCORE])
    fallback = FallbackScorer([first, second], clock=FakeClock())

    await fallback.score_vacancy(VACANCY, RESUME)
    assert second.calls == 1

    # cooldown не установлен: следующий вызов снова начинается с первого
    assert await fallback.score_vacancy(VACANCY, RESUME) is SCORE
    assert first.calls == 2
    assert second.calls == 1


async def test_all_failed_raises_with_names_and_reasons():
    first = FakeScorer("groq", [unavailable(60)])
    second = FakeScorer("openrouter", [ScoringError("кривой JSON")])
    fallback = FallbackScorer([first, second], clock=FakeClock())

    with pytest.raises(ScoringError, match="Все LLM-провайдеры недоступны") as excinfo:
        await fallback.score_vacancy(VACANCY, RESUME)
    message = str(excinfo.value)
    assert "groq" in message and "лимит" in message
    assert "openrouter" in message and "кривой JSON" in message


async def test_all_in_cooldown_tries_soonest_to_recover():
    clock = FakeClock()
    first = FakeScorer("groq", [unavailable(100)])
    second = FakeScorer("openrouter", [unavailable(50), SCORE])
    fallback = FallbackScorer([first, second], clock=clock)

    with pytest.raises(ScoringError):
        await fallback.score_vacancy(VACANCY, RESUME)  # оба упали → оба в cooldown

    # часы не сдвигались: оба ещё остывают, но openrouter остынет раньше — пробуем его
    assert await fallback.score_vacancy(VACANCY, RESUME) is SCORE
    assert first.calls == 1
    assert second.calls == 2


async def test_all_in_cooldown_soonest_fails_raises():
    clock = FakeClock()
    first = FakeScorer("groq", [unavailable(100)])
    second = FakeScorer("openrouter", [unavailable(50), unavailable(50)])
    fallback = FallbackScorer([first, second], clock=clock)

    with pytest.raises(ScoringError):
        await fallback.score_vacancy(VACANCY, RESUME)
    with pytest.raises(ScoringError, match="Все LLM-провайдеры недоступны"):
        await fallback.score_vacancy(VACANCY, RESUME)
    assert first.calls == 1  # groq остывает дольше — второй раз не трогали
    assert second.calls == 2


async def test_cover_letter_uses_same_fallback_logic():
    first = FakeScorer("groq", [unavailable(60)])
    second = FakeScorer("openrouter", ["Здравствуйте! Текст письма."])
    fallback = FallbackScorer([first, second], clock=FakeClock())

    letter = await fallback.write_cover_letter(VACANCY, RESUME, SCORE)

    assert letter == "Здравствуйте! Текст письма."
    assert first.calls == 1
    assert second.calls == 1


def test_empty_scorers_list_raises_value_error():
    with pytest.raises(ValueError):
        FallbackScorer([])


# ---------- make_scorer: сборка цепочки из providers.json ----------


def _settings(**overrides) -> Settings:
    values = {
        "llm_provider": "openai_compat",
        "llm_base_url": "https://api.groq.com/openai/v1",
        "llm_api_key": "test-key",
        "llm_providers_file": "__нет_такого_файла__.json",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def _write_providers(tmp_path, entries) -> str:
    path = tmp_path / "providers.json"
    path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_make_scorer_builds_chain_from_file(tmp_path):
    path = _write_providers(
        tmp_path,
        [
            {
                "name": "groq",
                "base_url": "https://api.groq.com/openai/v1",
                "api_key": "k1",
                "model": "llama-3.3-70b-versatile",
            },
            {
                "name": "openrouter",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": "k2",
                "model": "meta-llama/llama-3.3-70b-instruct:free",
            },
        ],
    )

    scorer = make_scorer(_settings(llm_providers_file=path))

    assert isinstance(scorer, FallbackScorer)
    assert [s.name for s in scorer._scorers] == ["groq", "openrouter"]
    assert scorer._scorers[1]._model == "meta-llama/llama-3.3-70b-instruct:free"
    assert scorer._scorers[1]._client.api_key == "k2"


def test_make_scorer_chain_entry_without_api_key(tmp_path):
    path = _write_providers(
        tmp_path,
        [{"name": "ollama", "base_url": "http://localhost:11434/v1", "model": "qwen3"}],
    )

    scorer = make_scorer(_settings(llm_providers_file=path))

    # отсутствующий api_key → "", а не глобальный LLM_API_KEY ("unused" — заглушка SDK)
    assert isinstance(scorer, FallbackScorer)
    assert scorer._scorers[0]._client.api_key == "unused"


def test_make_scorer_broken_json_exits(tmp_path, capsys):
    path = tmp_path / "providers.json"
    path.write_text("{битый json", encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        make_scorer(_settings(llm_providers_file=str(path)))

    assert excinfo.value.code == 1
    assert "providers.json" in capsys.readouterr().err


def test_make_scorer_empty_list_exits(tmp_path, capsys):
    path = _write_providers(tmp_path, [])

    with pytest.raises(SystemExit) as excinfo:
        make_scorer(_settings(llm_providers_file=path))

    assert excinfo.value.code == 1
    assert "непустой" in capsys.readouterr().err


def test_make_scorer_bad_entry_exits(tmp_path, capsys):
    path = _write_providers(tmp_path, [{"name": "groq", "лишнее_поле": 1}])

    with pytest.raises(SystemExit) as excinfo:
        make_scorer(_settings(llm_providers_file=path))

    assert excinfo.value.code == 1
    assert "providers.json" in capsys.readouterr().err


def test_make_scorer_without_file_returns_single_scorer(tmp_path):
    scorer = make_scorer(_settings(llm_providers_file=str(tmp_path / "missing.json")))
    assert isinstance(scorer, OpenAICompatScorer)
