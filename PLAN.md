# HH-Agent — план реализации

Личный ИИ-ассистент поиска работы на hh.ru: мониторит новые вакансии по сохранённым
запросам, оценивает каждую против резюме (LLM), пишет сопроводительные письма
и показывает карточки в веб-интерфейсе (React SPA, доступен с телефона). Ведёт
локальную воронку откликов.

> Интерфейс переехал с Telegram в веб (см. §2/§3): поллер и «мозг» не изменились —
> они общались с каналом только через `NotifierProto`/`StorageProto`, поэтому сменился
> лишь интерфейсный слой (`bot/` → `web/` + `frontend/`).

## 0. Изменение внешних условий (15.12.2025)

HeadHunter закрыл соискательский API: отклики (`/negotiations`), резюме (`/resumes/*`)
и OAuth-сценарии соискателя для сторонних приложений не работают. Открытым остаётся
поиск вакансий (`GET /vacancies`, `GET /vacancies/{id}`).

Следствия для дизайна (осознанно НЕ эмулируем веб-интерфейс — это нарушение правил
площадки и риск бана):
- **Резюме** — из локального файла (`RESUME_PATH`, по умолчанию `resume.md`).
- **Отклик** — вручную человеком: карточка содержит готовое письмо и URL-кнопку
  «Открыть на hh.ru»; кнопка «✅ Откликнулся» записывает факт в локальную воронку.
- **Воронка** — только локальная (`/status` без данных hh).
- OAuth-модуль удалён за ненадобностью.

Ценность агента сохраняется: мониторинг 24/7, скоринг, красные флаги, готовые письма —
уходит только автоматическое нажатие «откликнуться», что даже соответствует духу
нововведения hh (борьба со спам-откликами ботов).

## 1. Принцип архитектуры

Рутина (опрос API, дедупликация, расписание) — детерминированный Python-код.
LLM подключается по событиям: скоринг вакансии, генерация письма.
Необратимые действия совершает человек.

```
hh.ru API ──► Поллер (APScheduler, каждые N минут)  ← живёт в lifespan FastAPI
                 │  дедупликация по SQLite («уже видел»)
                 ▼  новые вакансии
            Скоринг (LLM, structured outputs → ScoreResult)
                 │  score >= порога → сопроводительное письмо
                 ▼
            WebNotifier → таблица cards (персист карточки)
                 ▼
            React SPA (за паролем) читает /api: фид карточек
            [🔗 Открыть на hh.ru] [✅ Откликнулся] [⏭ Пропустить] [⭐ В избранное]
                 │ «Откликнулся» — после ручного отклика на сайте
                 ▼
            локальная воронка (applications)
```

## 2. Структура проекта и зоны ответственности

```
hh-agent/
├── PLAN.md, README.md, pyproject.toml, .env.example, Dockerfile   [оркестратор]
├── src/hh_agent/
│   ├── __init__.py, config.py, models.py, interfaces.py           [оркестратор — КОНТРАКТЫ]
│   ├── hh/         client.py                                      [модуль hh-api]
│   ├── ai/         scorer.py, openai_scorer.py, fallback.py, prompts.py  [модуль ai-core]
│   ├── web/        app.py, routes.py, schemas.py, auth.py, notifier.py   [модуль web-интерфейс]
│   ├── db/         storage.py                                     [модуль core]
│   ├── composition.py (make_scorer), scheduler.py                 [модуль core]
│   └── main.py     (uvicorn-точка входа)                          [модуль core]
├── frontend/       Vite + React + TS + Tailwind (SPA, build → dist)  [модуль web-интерфейс]
└── tests/          test_hh_client / test_scoring / test_storage / test_scheduler /
                    test_storage_cards / test_web_notifier / test_web_api
```

## 3. Зафиксированные контракты

Модели — `models.py`; протоколы — `interfaces.py`; настройки — `config.py`.
Composition root (`main.py`) использует ровно эти имена:

| Модуль | Импорт | Конструктор |
|---|---|---|
| hh-клиент | `from hh_agent.hh.client import HHClient` | `HHClient(settings)` |
| скоринг/письма | `from hh_agent.composition import make_scorer` | `make_scorer(settings) -> ScorerProto` |
| веб-приложение | `from hh_agent.web.app import create_app` | `create_app() -> FastAPI` (поллер — в lifespan) |
| notifier (веб) | `from hh_agent.web.notifier import WebNotifier` | `WebNotifier(storage)` (реализует `NotifierProto`) |
| хранилище | `from hh_agent.db.storage import Storage` | `Storage(settings.db_path)` |

`HHClientProto` — только открытые методы: `search_vacancies`, `get_vacancy`.
`NotifierProto.send_vacancy_card(vacancy, score, letter, *, search_id=None)` и `send_text` —
единственный шов интерфейса; `WebNotifier` персистит карточку в `cards` и события в `events`.
Действия над карточкой — REST: `POST /api/cards/{id}/applied|skip|favorite`; отклик человек
делает по `vacancy.url`. Авторизация — единый пароль (`WEB_PASSWORD`) → сессионная кука.

## 4. Поток данных poll_once

1. Один раз за проход: резюме из файла `settings.resume_path` (нет файла →
   `notifier.send_text` с подсказкой создать `resume.md` и ранний выход).
2. Для каждого активного `SearchQuery`: зафиксировать `poll_started` ДО запроса;
   `hh.search_vacancies(query, date_from=last_polled_at)`.
3. Отфильтровать `storage.is_seen` → для новых: `hh.get_vacancy` → `scorer.score_vacancy`.
4. `storage.mark_seen(id, score)` — всегда.
5. `score.score >= settings.score_threshold` → `scorer.write_cover_letter` →
   `storage.save_letter` → `notifier.send_vacancy_card`.
6. `storage.touch_search(search.id, polled_at=poll_started)` — окно сдвигается на момент
   НАЧАЛА запроса (не конца обработки): вакансии, опубликованные во время скоринга,
   не теряются. Упавшая вакансия не помечается seen и обработается повторно, только
   если снова попадёт в выдачу (дедуп держится на is_seen, а не на date_from).
   Ошибка по вакансии — лог + продолжить; ошибка по поиску — last_polled_at не сдвигать.

## 5. Этапы

- **Этап 1 (текущий):** мониторинг + скоринг + письма + карточки + ручной отклик
  по URL-кнопке + локальная воронка (/status).
- **Этап 2:** управление поисками естественным языком, дневная сводка.
- **Этап 3:** бриф-подготовка к собеседованию (web search по компании), мок-интервью.
- **Этап 4:** аналитика воронки, тюнинг скоринга по обратной связи (skip/fav как сигнал).

## 6. Технологии и LLM-провайдеры

Бэкенд: Python 3.11+ (async), httpx, FastAPI + uvicorn, itsdangerous (сессионная кука),
aiosqlite, APScheduler, pytest + pytest-asyncio + respx.
Фронтенд: Vite + React + TypeScript + Tailwind CSS v4 (SPA, `frontend/dist` отдаёт FastAPI).

Скоринг и письма — за интерфейсом `ScorerProto`, реализация выбирается в main.py
по `settings.llm_provider`:

| Провайдер | Реализация | Импорт | Статус |
|---|---|---|---|
| `openai_compat` (дефолт) | бесплатные OpenAI-совместимые API: Groq / OpenRouter / Gemini compat / Ollama (base_url+model из настроек) | `from hh_agent.ai.openai_scorer import OpenAICompatScorer` — `OpenAICompatScorer(settings)` | активен |
| `anthropic` | anthropic ≥0.116, `claude-opus-4-8`, structured outputs via `messages.parse` | `from hh_agent.ai.scorer import ClaudeScorer` — `ClaudeScorer(settings)` | платный, по умолчанию выключен |

У `openai_compat` нет серверной гарантии схемы: JSON-режим (`response_format
json_object` где поддерживается) + схема в промпте + pydantic-валидация с одним
ретраем; при невалидном ответе — `ScoringError`.

### Failover-цепочка провайдеров

Если существует файл `settings.llm_providers_file` (`providers.json`, в .gitignore;
образец — `providers.json.example`), main.py строит цепочку: список
`OpenAICompatScorer` (по одному на запись: name, base_url, api_key, model) внутри
обёртки `FallbackScorer` (`from hh_agent.ai.fallback import FallbackScorer`),
реализующей тот же `ScorerProto`. Семантика:

- Ошибки классифицируются: `ProviderUnavailableError(ScoringError)` — 429 / 5xx /
  сетевые (провайдер исчерпан или лёг) → провайдер уходит в cooldown
  (retry-after из ответа, иначе дефолт: 429 → 15 мин, 5xx/сеть → 5 мин) и цепочка
  переходит к следующему; прочие `ScoringError` (кривой JSON после ретрая) →
  следующий провайдер БЕЗ cooldown.
- Провайдеры в cooldown пропускаются; когда остыли — снова участвуют с начала списка.
- Все провайдеры недоступны → `ScoringError`; поллер не помечает вакансию seen,
  она дообработается следующим проходом.
- Каждое переключение — в лог (logging.warning, с именем провайдера и причиной).

Файла нет → одиночный `OpenAICompatScorer` из LLM_*-настроек (обратная совместимость).

## 6.1. Источники вакансий

`HHClientProto` (search_vacancies + get_vacancy) — фактически протокол «источника
вакансий»; main.py выбирает реализацию по `settings.vacancy_source`:

| Источник | Реализация | Условие |
|---|---|---|
| hh.ru | `from hh_agent.hh.client import HHClient` | требует app-креды (заявка на dev.hh.ru модерируется до 15 раб. дней) |
| Работа России | `from hh_agent.trudvsem.client import TrudvsemClient` — `TrudvsemClient(settings)` | открытый API opendata.trudvsem.ru, без регистрации |

`auto` (дефолт): hh при заполненных кредах, иначе trudvsem (с log.info о выборе).
ID вакансий trudvsem префиксуются `tv-` в таблице seen, чтобы не пересекаться с hh.

## 7. Ограничения и риски

- Вебхуков у hh нет — поллинг; заголовок `HH-User-Agent` обязателен на каждый запрос.
- Соискательский API закрыт (см. §0) — никаких обходов через эмуляцию веб-интерфейса.
- Если hh ограничит и анонимный поиск (потребует app-токен) — добавить client
  credentials в HHClient (расширение, не слом архитектуры).
