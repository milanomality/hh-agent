"""Тесты ИИ-модуля. Anthropic API не вызывается: клиент полностью замокан."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import anthropic
import httpx
import pydantic
import pytest

from hh_agent.ai.scorer import ClaudeScorer, ScoringError
from hh_agent.config import Settings
from hh_agent.models import Employer, Resume, ScoreResult, Vacancy, Verdict

SCORE = ScoreResult(
    score=8,
    verdict=Verdict.apply,
    summary="Стек и опыт кандидата закрывают основные требования.",
    matches=["Python (asyncio)", "опыт с REST API"],
    gaps=["Kubernetes"],
    red_flags=[],
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


@pytest.fixture
def scorer() -> ClaudeScorer:
    settings = Settings(anthropic_api_key="test-key", _env_file=None)
    s = ClaudeScorer(settings)
    # подменяем клиент целиком — ни один тест не ходит в сеть
    s._client = SimpleNamespace(messages=SimpleNamespace(parse=AsyncMock(), create=AsyncMock()))
    return s


def parse_response(stop_reason: str = "end_turn", parsed_output: ScoreResult | None = SCORE):
    return SimpleNamespace(stop_reason=stop_reason, parsed_output=parsed_output)


def create_response(text: str, stop_reason: str = "end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
    )


def validation_error() -> pydantic.ValidationError:
    try:
        ScoreResult.model_validate_json("{это не json")
    except pydantic.ValidationError as exc:
        return exc
    raise AssertionError("ожидалась ValidationError")


# ---------- score_vacancy ----------


async def test_score_vacancy_returns_parsed_score_result(scorer, vacancy, resume):
    scorer._client.messages.parse.return_value = parse_response()

    result = await scorer.score_vacancy(vacancy, resume)

    assert isinstance(result, ScoreResult)
    assert result.score == 8
    assert result.verdict is Verdict.apply

    kwargs = scorer._client.messages.parse.await_args.kwargs
    assert kwargs["output_format"] is ScoreResult
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["max_tokens"] == 16000
    assert kwargs["model"] == "claude-opus-4-8"


async def test_scoring_prompt_contains_resume_and_vacancy(scorer, vacancy, resume):
    scorer._client.messages.parse.return_value = parse_response()

    await scorer.score_vacancy(vacancy, resume)

    content = scorer._client.messages.parse.await_args.kwargs["messages"][0]["content"]
    assert "<resume>" in content and "</resume>" in content
    assert "<vacancy>" in content and "</vacancy>" in content
    assert resume.text in content
    assert vacancy.description in content
    assert vacancy.name in content


async def test_score_refusal_raises_scoring_error(scorer, vacancy, resume):
    scorer._client.messages.parse.return_value = parse_response(
        stop_reason="refusal", parsed_output=None
    )
    with pytest.raises(ScoringError, match="refusal"):
        await scorer.score_vacancy(vacancy, resume)


async def test_score_max_tokens_raises_scoring_error(scorer, vacancy, resume):
    scorer._client.messages.parse.return_value = parse_response(stop_reason="max_tokens")
    with pytest.raises(ScoringError, match="лимиту токенов"):
        await scorer.score_vacancy(vacancy, resume)


async def test_score_invalid_model_output_raises_scoring_error(scorer, vacancy, resume):
    # messages.parse валидирует JSON ответа внутри вызова и бросает ValidationError
    scorer._client.messages.parse.side_effect = validation_error()
    with pytest.raises(ScoringError, match="не удалось разобрать"):
        await scorer.score_vacancy(vacancy, resume)


async def test_score_missing_parsed_output_raises_scoring_error(scorer, vacancy, resume):
    scorer._client.messages.parse.return_value = parse_response(parsed_output=None)
    with pytest.raises(ScoringError):
        await scorer.score_vacancy(vacancy, resume)


async def test_score_api_errors_wrapped_in_scoring_error(scorer, vacancy, resume):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    scorer._client.messages.parse.side_effect = anthropic.RateLimitError(
        "rate limited",
        response=httpx.Response(429, request=request),
        body=None,
    )
    with pytest.raises(ScoringError, match="лимит запросов"):
        await scorer.score_vacancy(vacancy, resume)

    scorer._client.messages.parse.side_effect = anthropic.APIConnectionError(request=request)
    with pytest.raises(ScoringError, match="соединиться"):
        await scorer.score_vacancy(vacancy, resume)


# ---------- write_cover_letter ----------


async def test_cover_letter_returns_plain_stripped_text(scorer, vacancy, resume):
    raw = "\n\nЗдравствуйте! Пишу по поводу вакансии Python-разработчика.\n\n"
    scorer._client.messages.create.return_value = create_response(raw)

    letter = await scorer.write_cover_letter(vacancy, resume, SCORE)

    assert isinstance(letter, str)
    assert letter == raw.strip()
    assert not letter.startswith(("```", "#", "*"))

    kwargs = scorer._client.messages.create.await_args.kwargs
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["model"] == "claude-opus-4-8"


async def test_cover_letter_prompt_contains_matches_and_texts(scorer, vacancy, resume):
    scorer._client.messages.create.return_value = create_response("Текст письма.")

    await scorer.write_cover_letter(vacancy, resume, SCORE)

    content = scorer._client.messages.create.await_args.kwargs["messages"][0]["content"]
    assert resume.text in content
    assert vacancy.description in content
    for match in SCORE.matches:
        assert match in content


async def test_cover_letter_refusal_raises_scoring_error(scorer, vacancy, resume):
    scorer._client.messages.create.return_value = create_response("", stop_reason="refusal")
    with pytest.raises(ScoringError, match="refusal"):
        await scorer.write_cover_letter(vacancy, resume, SCORE)


async def test_cover_letter_empty_text_raises_scoring_error(scorer, vacancy, resume):
    scorer._client.messages.create.return_value = create_response("   ")
    with pytest.raises(ScoringError, match="пустое"):
        await scorer.write_cover_letter(vacancy, resume, SCORE)
