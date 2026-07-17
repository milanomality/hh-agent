# ── Стадия 1: сборка React-SPA ──────────────────────────────────────────────
FROM node:20-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── Стадия 2: Python-рантайм ─────────────────────────────────────────────────
FROM python:3.11-slim
WORKDIR /app

# Устанавливаем пакет (без dev-зависимостей)
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# Собранный фронтенд из стадии 1; путь передаём явно (пакет установлен в site-packages,
# автопоиск dist рядом с исходниками в контейнере не сработает)
COPY --from=frontend /app/frontend/dist ./frontend/dist
ENV FRONTEND_DIST=/app/frontend/dist

# Рантайм-состояние (resume.md, hh_agent.db, .tokens.json, providers.json, .env)
# монтируйте томами — в образ не кладём.
EXPOSE 8000

# Один воркер uvicorn: одно sqlite-соединение и один поллер.
CMD ["python", "-m", "hh_agent.main"]
