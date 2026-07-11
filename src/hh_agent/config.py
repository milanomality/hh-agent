from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки приложения; читаются из .env / переменных окружения."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # hh.ru — только открытое API (поиск); соискательский API закрыт с 15.12.2025
    hh_user_agent: str = "hh-agent/0.1 (dev@example.com)"

    # Резюме — локальный файл (plain text / markdown)
    resume_path: str = "resume.md"

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: int = 0

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
