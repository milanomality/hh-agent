"""Тесты OpenAI-совместимого скорера. Сеть не вызывается: клиент полностью замокан."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai
import pytest

from hh_agent.ai.openai_scorer import OpenAICompatScorer, ProviderUnavailableError
from hh_agent.ai.scorer import ClaudeScorer, ScoringError
from hh_agent.config import Settings
from hh_agent.main import make_scorer
from hh_agent.models import Employer, Resume, ScoreResult, Vacancy, Verdict

SCORE = ScoreResult(
    score=8,
    verdict=Verdict.apply,
    summary="Стек и опыт кандидата закрывают основные требования.",
    matches=["Python (asyncio)", "опыт с REST API"],
    gaps=["Kubernetes"],
    red_flags=[],
)

SCORE_JSON = json.dumps(
    {
        "score": 8,
        "verdict": "apply",
        "summary": "Стек и опыт кандидата закрывают основные требования.",
        "matches": ["Python (asyncio)"],
        "gaps": ["Kubernetes"],
        "red_flags": [],
    },
    ensure_ascii=False,
)


@pytest.fixture
def vacancy() -> Vacancy:
    return Vacancy(
        id="123",
        name="Python-разработчик",
        employer=Employer(id="1", name="Рога и Копыта"),
        salary_text="от 150 000 ₽",
        area_name="Калининград",
        description="Разработка асинхронных сервисов на Python, httpx, PostgreSQL.",
        key_skills=["Python", "asyncio"],
    )


@pytest.fixture
def resume() -> Resume:
    return Resume(
        id="r1",
        title="Python-разработчик",
        text="Три года пишу асинхронный Python: httpx, aiogram, SQLite. Пет-проекты на GitHub.",
    )


def _scorer_settings() -> Settings:
    return Settings(
        llm_provider="openai_compat",
        llm_base_url="https://api.groq.com/openai/v1",
        llm_api_key="test-key",
        llm_model="llama-3.3-70b-versatile",
        _env_file=None,
    )


@pytest.fixture
def scorer() -> OpenAICompatScorer:
    s = OpenAICompatScorer(_scorer_settings())
    # подменяем клиент целиком — ни один тест не ходит в сеть
    s._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock()))
    )
    return s


def chat_response(content: str | None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def api_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")


def bad_request_error() -> openai.BadRequestError:
    return openai.BadRequestError(
        "response_format is not supported",
        response=httpx.Response(400, request=api_request()),
        body=None,
    )


# ---------- score_vacancy ----------


async def test_score_valid_json_returns_score_result(scorer, vacancy, resume):
    scorer._client.chat.completions.create.return_value = chat_response(SCORE_JSON)

    result = await scorer.score_vacancy(vacancy, resume)

    assert isinstance(result, ScoreResult)
    assert result.score == 8
    assert result.verdict is Verdict.apply

    kwargs = scorer._client.chat.completions.create.await_args.kwargs
    assert kwargs["model"] == "llama-3.3-70b-versatile"
    assert kwargs["max_tokens"] == 4096
    assert kwargs["response_format"] == {"type": "json_object"}
    assert "temperature" not in kwargs


async def test_scoring_messages_contain_schema_resume_and_vacancy(scorer, vacancy, resume):
    scorer._client.chat.completions.create.return_value = chat_response(SCORE_JSON)

    await scorer.score_vacancy(vacancy, resume)

    messages = scorer._client.chat.completions.create.await_args.kwargs["messages"]
    system, user = messages[0], messages[1]
    assert system["role"] == "system"
    assert "JSON" in system["content"]                    # требование схемы
    assert "карьерный консультант" in system["content"]   # исходный системный промпт
    for field in ("score", "verdict", "summary", "matches", "gaps", "red_flags"):
        assert field in system["content"]
    assert user["role"] == "user"
    assert resume.text in user["content"]
    assert vacancy.description in user["content"]


async def test_score_json_in_markdown_fences_parsed(scorer, vacancy, resume):
    scorer._client.chat.completions.create.return_value = chat_response(
        f"```json\n{SCORE_JSON}\n```"
    )

    result = await scorer.score_vacancy(vacancy, resume)

    assert result.score == 8
    assert scorer._client.chat.completions.create.await_count == 1


async def test_score_json_with_surrounding_text_parsed(scorer, vacancy, resume):
    scorer._client.chat.completions.create.return_value = chat_response(
        f"Вот оценка вакансии:\n{SCORE_JSON}\nНадеюсь, помог!"
    )

    result = await scorer.score_vacancy(vacancy, resume)

    assert result.verdict is Verdict.apply


async def test_score_response_format_rejected_retries_without_it(scorer, vacancy, resume):
    scorer._client.chat.completions.create.side_effect = [
        bad_request_error(),
        chat_response(SCORE_JSON),
    ]

    result = await scorer.score_vacancy(vacancy, resume)

    assert result.score == 8
    calls = scorer._client.chat.completions.create.await_args_list
    assert len(calls) == 2
    assert calls[0].kwargs["response_format"] == {"type": "json_object"}
    assert "response_format" not in calls[1].kwargs


async def test_score_invalid_json_retried_with_error_message(scorer, vacancy, resume):
    scorer._client.chat.completions.create.side_effect = [
        chat_response("это вообще не JSON"),
        chat_response(SCORE_JSON),
    ]

    result = await scorer.score_vacancy(vacancy, resume)

    assert result.score == 8
    retry_messages = scorer._client.chat.completions.create.await_args_list[1].kwargs["messages"]
    # в ретрае диалог дополнен ответом модели и требованием исправить
    assert retry_messages[-2]["role"] == "assistant"
    assert retry_messages[-2]["content"] == "это вообще не JSON"
    assert retry_messages[-1]["role"] == "user"
    assert "валидац" in retry_messages[-1]["content"]
    assert "JSON" in retry_messages[-1]["content"]


async def test_score_invalid_json_twice_raises_scoring_error(scorer, vacancy, resume):
    scorer._client.chat.completions.create.side_effect = [
        chat_response("мусор"),
        chat_response('{"score": "тоже мусор"}'),
    ]

    with pytest.raises(ScoringError, match="дважды"):
        await scorer.score_vacancy(vacancy, resume)

    # ровно один ретрай — третьего запроса нет
    assert scorer._client.chat.completions.create.await_count == 2


async def test_score_rate_limit_raises_provider_unavailable(scorer, vacancy, resume):
    scorer._client.chat.completions.create.side_effect = openai.RateLimitError(
        "rate limited",
        response=httpx.Response(429, request=api_request()),
        body=None,
    )
    with pytest.raises(ProviderUnavailableError, match="лимит запросов") as excinfo:
        await scorer.score_vacancy(vacancy, resume)
    assert excinfo.value.cooldown_seconds == 900  # retry-after нет — дефолт


@pytest.mark.parametrize(
    ("retry_after", "expected"),
    [
        ("37", 37.0),                              # число секунд
        ("1.5", 1.5),                              # дробное — тоже число
        ("Wed, 21 Oct 2026 07:28:00 GMT", 900.0),  # HTTP-дата не разбирается — дефолт
        ("-5", 900.0),                             # отрицательное — дефолт
    ],
)
async def test_score_rate_limit_retry_after_header(
    scorer, vacancy, resume, retry_after, expected
):
    scorer._client.chat.completions.create.side_effect = openai.RateLimitError(
        "rate limited",
        response=httpx.Response(
            429, headers={"retry-after": retry_after}, request=api_request()
        ),
        body=None,
    )
    with pytest.raises(ProviderUnavailableError) as excinfo:
        await scorer.score_vacancy(vacancy, resume)
    assert excinfo.value.cooldown_seconds == expected


async def test_score_5xx_raises_provider_unavailable(scorer, vacancy, resume):
    scorer._client.chat.completions.create.side_effect = openai.APIStatusError(
        "server error",
        response=httpx.Response(500, request=api_request()),
        body=None,
    )
    with pytest.raises(ProviderUnavailableError, match="500") as excinfo:
        await scorer.score_vacancy(vacancy, resume)
    assert excinfo.value.cooldown_seconds == 300


async def test_score_connection_error_raises_provider_unavailable(scorer, vacancy, resume):
    scorer._client.chat.completions.create.side_effect = openai.APIConnectionError(
        request=api_request()
    )
    with pytest.raises(ProviderUnavailableError, match="соединиться") as excinfo:
        await scorer.score_vacancy(vacancy, resume)
    assert excinfo.value.cooldown_seconds == 300


async def test_score_4xx_raises_plain_scoring_error(scorer, vacancy, resume):
    scorer._client.chat.completions.create.side_effect = openai.APIStatusError(
        "forbidden",
        response=httpx.Response(403, request=api_request()),
        body=None,
    )
    with pytest.raises(ScoringError, match="403") as excinfo:
        await scorer.score_vacancy(vacancy, resume)
    # клиентская ошибка — не повод для cooldown провайдера
    assert not isinstance(excinfo.value, ProviderUnavailableError)


async def test_score_empty_choices_raises_scoring_error(scorer, vacancy, resume):
    scorer._client.chat.completions.create.return_value = SimpleNamespace(choices=[])
    with pytest.raises(ScoringError, match="choices"):
        await scorer.score_vacancy(vacancy, resume)


async def test_score_none_content_raises_scoring_error(scorer, vacancy, resume):
    scorer._client.chat.completions.create.return_value = chat_response(None)
    with pytest.raises(ScoringError, match="пустой ответ"):
        await scorer.score_vacancy(vacancy, resume)


# ---------- write_cover_letter ----------


async def test_cover_letter_returns_plain_stripped_text(scorer, vacancy, resume):
    raw = "\n\nЗдравствуйте! Пишу по поводу вакансии Python-разработчика.\n\n"
    scorer._client.chat.completions.create.return_value = chat_response(raw)

    letter = await scorer.write_cover_letter(vacancy, resume, SCORE)

    assert letter == raw.strip()
    kwargs = scorer._client.chat.completions.create.await_args.kwargs
    assert "response_format" not in kwargs  # JSON-режим только для скоринга
    assert resume.text in kwargs["messages"][1]["content"]
    for match in SCORE.matches:
        assert match in kwargs["messages"][1]["content"]


async def test_cover_letter_fences_stripped(scorer, vacancy, resume):
    scorer._client.chat.completions.create.return_value = chat_response(
        "```\nЗдравствуйте! Текст письма.\n```"
    )

    letter = await scorer.write_cover_letter(vacancy, resume, SCORE)

    assert letter == "Здравствуйте! Текст письма."
    assert "```" not in letter


async def test_cover_letter_empty_text_raises_scoring_error(scorer, vacancy, resume):
    scorer._client.chat.completions.create.return_value = chat_response("   ")
    with pytest.raises(ScoringError, match="пустое"):
        await scorer.write_cover_letter(vacancy, resume, SCORE)


# ---------- конструктор: overrides для failover-цепочки ----------


def test_scorer_name_derived_from_base_url_host():
    assert OpenAICompatScorer(_scorer_settings()).name == "api.groq.com"


def test_scorer_constructor_overrides():
    s = OpenAICompatScorer(
        _scorer_settings(),
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="or-key",
        model="meta-llama/llama-3.3-70b-instruct:free",
    )
    assert s.name == "openrouter"
    assert s._model == "meta-llama/llama-3.3-70b-instruct:free"
    assert s._client.api_key == "or-key"
    assert str(s._client.base_url).startswith("https://openrouter.ai/api/v1")


def test_scorer_none_overrides_fall_back_to_settings():
    s = OpenAICompatScorer(_scorer_settings(), name=None, base_url=None, model=None)
    assert s.name == "api.groq.com"
    assert s._model == "llama-3.3-70b-versatile"
    assert s._client.api_key == "test-key"  # api_key=None → из settings


def test_scorer_empty_api_key_override_uses_sdk_stub():
    # пустой ключ из providers.json (Ollama) — не подменяется глобальным LLM_API_KEY
    s = OpenAICompatScorer(_scorer_settings(), api_key="")
    assert s._client.api_key == "unused"


# ---------- make_scorer (выбор реализации в main) ----------


def _settings(**overrides) -> Settings:
    values = {
        "llm_provider": "openai_compat",
        "llm_base_url": "https://api.groq.com/openai/v1",
        "llm_api_key": "test-key",
        "anthropic_api_key": "test-key",
        # не подхватывать реальный providers.json из cwd — тестам нужен одиночный скорер
        "llm_providers_file": "__нет_такого_файла__.json",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def test_make_scorer_openai_compat_default():
    assert isinstance(make_scorer(_settings()), OpenAICompatScorer)


def test_make_scorer_anthropic():
    assert isinstance(make_scorer(_settings(llm_provider="anthropic")), ClaudeScorer)


def test_make_scorer_unknown_provider_exits(capsys):
    with pytest.raises(SystemExit) as excinfo:
        make_scorer(_settings(llm_provider="нечто"))
    assert excinfo.value.code == 1
    assert "LLM_PROVIDER" in capsys.readouterr().err


def test_make_scorer_warns_on_empty_key_for_remote_url(caplog):
    with caplog.at_level(logging.WARNING, logger="hh_agent.main"):
        scorer = make_scorer(_settings(llm_api_key=""))
    assert isinstance(scorer, OpenAICompatScorer)
    assert any("LLM_API_KEY" in r.getMessage() for r in caplog.records)


def test_make_scorer_no_warning_for_localhost_without_key(caplog):
    with caplog.at_level(logging.WARNING, logger="hh_agent.main"):
        make_scorer(
            _settings(llm_api_key="", llm_base_url="http://localhost:11434/v1")
        )
    assert not [r for r in caplog.records if "LLM_API_KEY" in r.getMessage()]
