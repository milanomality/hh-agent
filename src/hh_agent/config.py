from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки приложения; читаются из .env / переменных окружения."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # hh.ru — поиск через API с авторизацией приложения (client_credentials);
    # соискательский API закрыт с 15.12.2025. Регистрация приложения: https://dev.hh.ru
    hh_user_agent: str = "hh-agent/0.1 (dev@example.com)"
    hh_client_id: str = ""
    hh_client_secret: str = ""
    # app-токен кэшируется на диск, чтобы переживать рестарты: hh троттлит
    # повторную выдачу («app token refresh too early»). Файл — в .gitignore.
    hh_token_cache_path: str = ".tokens.json"

    # Источник вакансий: auto = hh при заполненных hh_client_id/secret, иначе
    # «Работа России» (opendata.trudvsem.ru — открытый API без регистрации).
    vacancy_source: str = "auto"  # auto | hh | trudvsem

    # Резюме — локальный файл (plain text / markdown)
    resume_path: str = "resume.md"

    # Веб-интерфейс (пришёл на смену Telegram). web_password/web_secret обязательны
    # при старте (проверяется в lifespan). Доступ удалённый → авторизация по паролю.
    web_password: str = ""            # единый пароль на вход
    web_secret: str = ""              # секрет подписи сессионной куки (itsdangerous)
    web_host: str = "0.0.0.0"
    web_port: int = 8000
    web_secure_cookie: bool = True    # кука только по HTTPS; для голого LAN-теста → False
    frontend_dist: str = ""           # путь к собранному SPA (пусто → dist рядом с пакетом)

    # LLM-провайдер. По умолчанию — бесплатный OpenAI-совместимый (Groq / OpenRouter /
    # Gemini compat / локальная Ollama — задаётся base_url + model).
    llm_provider: str = "openai_compat"  # openai_compat | anthropic
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_key: str = ""
    llm_model: str = "llama-3.3-70b-versatile"

    # Failover-цепочка бесплатных провайдеров: если файл существует — используется
    # список из него (в порядке приоритета) вместо одиночных LLM_*-настроек выше.
    llm_providers_file: str = "providers.json"

    # Anthropic — платный путь, по умолчанию не используется (llm_provider=anthropic)
    anthropic_api_key: str = ""
    claude_model: str = "claude-opus-4-8"

    # Поведение
    poll_interval_minutes: int = 20
    score_threshold: int = 7
    db_path: str = "hh_agent.db"
