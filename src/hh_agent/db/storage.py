"""SQLite-хранилище состояния агента (реализация StorageProto)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import aiosqlite

from ..models import (
    Application,
    Card,
    CardStatus,
    Employer,
    Event,
    ScoreResult,
    SearchQuery,
    Vacancy,
    Verdict,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    area TEXT,
    salary_from INTEGER,
    experience TEXT,
    schedule TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    last_polled_at TEXT
);
CREATE TABLE IF NOT EXISTS seen (
    vacancy_id TEXT PRIMARY KEY,
    score INTEGER,
    verdict TEXT,
    favorite INTEGER DEFAULT 0,
    seen_at TEXT
);
CREATE TABLE IF NOT EXISTS letters (
    vacancy_id TEXT PRIMARY KEY,
    letter TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applications (
    vacancy_id TEXT NOT NULL,
    resume_id TEXT NOT NULL,
    letter TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS cards (
    vacancy_id    TEXT PRIMARY KEY,
    search_id     INTEGER,
    name          TEXT NOT NULL,
    employer_id   TEXT,
    employer_name TEXT,
    salary_text   TEXT,
    area_name     TEXT,
    url           TEXT,
    published_at  TEXT,
    description   TEXT,
    key_skills    TEXT,
    score         INTEGER,
    verdict       TEXT,
    summary       TEXT,
    matches       TEXT,
    gaps          TEXT,
    red_flags     TEXT,
    letter        TEXT,
    status        TEXT NOT NULL DEFAULT 'new',
    favorite      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT,
    applied_at    TEXT,
    skipped_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_cards_status  ON cards(status);
CREATE INDEX IF NOT EXISTS idx_cards_created ON cards(created_at);
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    level      TEXT NOT NULL DEFAULT 'info',
    text       TEXT NOT NULL,
    created_at TEXT
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _dump_list(items: list[str] | None) -> str:
    return json.dumps(items or [], ensure_ascii=False)


def _load_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        data = json.loads(value)
    except (ValueError, TypeError):
        return []
    return [str(x) for x in data] if isinstance(data, list) else []


def _safe_enum(enum_cls, value, default):
    """Значение enum из БД; неизвестное/NULL (например, порча данных) → default."""
    try:
        return enum_cls(value)
    except ValueError:
        return default


def _row_to_card(r: aiosqlite.Row) -> Card:
    return Card(
        vacancy_id=r["vacancy_id"],
        search_id=r["search_id"],
        name=r["name"],
        employer=Employer(id=r["employer_id"], name=r["employer_name"] or ""),
        salary_text=r["salary_text"] or "не указана",
        area_name=r["area_name"] or "",
        url=r["url"] or "",
        published_at=_parse_dt(r["published_at"]),
        description=r["description"] or "",
        key_skills=_load_list(r["key_skills"]),
        score=r["score"] or 0,
        verdict=_safe_enum(Verdict, r["verdict"], Verdict.maybe),
        summary=r["summary"] or "",
        matches=_load_list(r["matches"]),
        gaps=_load_list(r["gaps"]),
        red_flags=_load_list(r["red_flags"]),
        letter=r["letter"] or "",
        status=_safe_enum(CardStatus, r["status"], CardStatus.new),
        favorite=bool(r["favorite"]),
        created_at=_parse_dt(r["created_at"]),
        applied_at=_parse_dt(r["applied_at"]),
        skipped_at=_parse_dt(r["skipped_at"]),
    )


class Storage:
    """Хранилище на aiosqlite: одно соединение на процесс.

    aiosqlite сериализует команды через собственный поток; asyncio.Lock
    дополнительно защищает многошаговые операции (execute + commit)
    от чередования между задачами.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        """Открыть соединение и создать схему (идемпотентно)."""
        if self._db is None:
            self._db = await aiosqlite.connect(self._db_path)
            self._db.row_factory = aiosqlite.Row
        # WAL: конкурентные веб-чтения не блокируют друг друга и редкую запись поллера
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Storage не инициализирован: вызовите await init()")
        return self._db

    async def _write(self, sql: str, params: tuple = ()) -> int | None:
        """Выполнить запись под локом; вернуть lastrowid."""
        async with self._lock:
            cur = await self._conn.execute(sql, params)
            await self._conn.commit()
            lastrowid = cur.lastrowid
            await cur.close()
            return lastrowid

    # --- дедупликация вакансий -------------------------------------------

    async def is_seen(self, vacancy_id: str) -> bool:
        async with self._conn.execute(
            "SELECT 1 FROM seen WHERE vacancy_id = ?", (vacancy_id,)
        ) as cur:
            return await cur.fetchone() is not None

    async def mark_seen(self, vacancy_id: str, score: ScoreResult | None = None) -> None:
        # favorite при повторной пометке не трогаем
        await self._write(
            """INSERT INTO seen (vacancy_id, score, verdict, seen_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(vacancy_id) DO UPDATE SET
                   score = excluded.score, verdict = excluded.verdict, seen_at = excluded.seen_at""",
            (
                vacancy_id,
                score.score if score else None,
                score.verdict.value if score else None,
                _now_iso(),
            ),
        )

    async def set_favorite(self, vacancy_id: str, fav: bool = True) -> None:
        await self._write(
            """INSERT INTO seen (vacancy_id, favorite, seen_at) VALUES (?, ?, ?)
               ON CONFLICT(vacancy_id) DO UPDATE SET favorite = excluded.favorite""",
            (vacancy_id, int(fav), _now_iso()),
        )

    # --- сопроводительные письма ------------------------------------------

    async def save_letter(self, vacancy_id: str, letter: str) -> None:
        await self._write(
            "INSERT OR REPLACE INTO letters (vacancy_id, letter) VALUES (?, ?)",
            (vacancy_id, letter),
        )

    async def get_letter(self, vacancy_id: str) -> str | None:
        async with self._conn.execute(
            "SELECT letter FROM letters WHERE vacancy_id = ?", (vacancy_id,)
        ) as cur:
            row = await cur.fetchone()
        return row["letter"] if row else None

    # --- поисковые запросы --------------------------------------------------

    async def list_searches(self, only_active: bool = True) -> list[SearchQuery]:
        sql = "SELECT * FROM searches"
        if only_active:
            sql += " WHERE active = 1"
        sql += " ORDER BY id"
        async with self._conn.execute(sql) as cur:
            rows = await cur.fetchall()
        return [
            SearchQuery(
                id=r["id"],
                text=r["text"],
                area=r["area"],
                salary_from=r["salary_from"],
                experience=r["experience"],
                schedule=r["schedule"],
                active=bool(r["active"]),
                last_polled_at=_parse_dt(r["last_polled_at"]),
            )
            for r in rows
        ]

    async def add_search(self, query: SearchQuery) -> SearchQuery:
        rowid = await self._write(
            """INSERT INTO searches (text, area, salary_from, experience, schedule, active, last_polled_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                query.text,
                query.area,
                query.salary_from,
                query.experience,
                query.schedule,
                int(query.active),
                query.last_polled_at.isoformat() if query.last_polled_at else None,
            ),
        )
        return query.model_copy(update={"id": rowid})

    async def deactivate_search(self, search_id: int) -> None:
        await self._write("UPDATE searches SET active = 0 WHERE id = ?", (search_id,))

    async def touch_search(self, search_id: int, polled_at: datetime | None = None) -> None:
        """Сдвигает last_polled_at: None — текущее время UTC, иначе переданный момент."""
        await self._write(
            "UPDATE searches SET last_polled_at = ? WHERE id = ?",
            (polled_at.isoformat() if polled_at else _now_iso(), search_id),
        )

    # --- воронка откликов -----------------------------------------------------

    async def save_application(self, app: Application) -> None:
        await self._write(
            """INSERT INTO applications (vacancy_id, resume_id, letter, state, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                app.vacancy_id,
                app.resume_id,
                app.letter,
                app.state,
                app.created_at.isoformat() if app.created_at else _now_iso(),
            ),
        )

    async def list_applications(self) -> list[Application]:
        async with self._conn.execute(
            "SELECT * FROM applications ORDER BY created_at"
        ) as cur:
            rows = await cur.fetchall()
        return [
            Application(
                vacancy_id=r["vacancy_id"],
                resume_id=r["resume_id"],
                letter=r["letter"],
                state=r["state"],
                created_at=_parse_dt(r["created_at"]),
            )
            for r in rows
        ]

    # --- карточки оценённых вакансий (веб-фид) --------------------------------

    async def save_card(
        self,
        vacancy: Vacancy,
        score: ScoreResult,
        letter: str | None,
        *,
        search_id: int | None = None,
    ) -> None:
        """Сохраняет карточку. При повторной отправке обновляет только контент —
        status/favorite/*_at не трогаем, чтобы не затереть действия пользователя."""
        await self._write(
            """INSERT INTO cards (
                   vacancy_id, search_id, name, employer_id, employer_name, salary_text,
                   area_name, url, published_at, description, key_skills, score, verdict,
                   summary, matches, gaps, red_flags, letter, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(vacancy_id) DO UPDATE SET
                   search_id = excluded.search_id, name = excluded.name,
                   employer_id = excluded.employer_id, employer_name = excluded.employer_name,
                   salary_text = excluded.salary_text, area_name = excluded.area_name,
                   url = excluded.url, published_at = excluded.published_at,
                   description = excluded.description, key_skills = excluded.key_skills,
                   score = excluded.score, verdict = excluded.verdict,
                   summary = excluded.summary, matches = excluded.matches,
                   gaps = excluded.gaps, red_flags = excluded.red_flags,
                   letter = excluded.letter""",
            (
                vacancy.id,
                search_id,
                vacancy.name,
                vacancy.employer.id,
                vacancy.employer.name,
                vacancy.salary_text,
                vacancy.area_name,
                vacancy.url,
                vacancy.published_at.isoformat() if vacancy.published_at else None,
                vacancy.description,
                _dump_list(vacancy.key_skills),
                score.score,
                score.verdict.value,
                score.summary,
                _dump_list(score.matches),
                _dump_list(score.gaps),
                _dump_list(score.red_flags),
                letter or "",
                _now_iso(),
            ),
        )

    async def get_card(self, vacancy_id: str) -> Card | None:
        async with self._conn.execute(
            "SELECT * FROM cards WHERE vacancy_id = ?", (vacancy_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_card(row) if row else None

    @staticmethod
    def _card_filters(
        min_score: int | None,
        favorite: bool | None,
        status: CardStatus | str | None,
        search_id: int | None,
    ) -> tuple[list[str], list[object]]:
        where: list[str] = []
        params: list[object] = []
        if min_score is not None:
            where.append("score >= ?")
            params.append(min_score)
        if favorite is not None:
            where.append("favorite = ?")
            params.append(int(favorite))
        if status is not None:
            where.append("status = ?")
            params.append(status.value if isinstance(status, CardStatus) else status)
        if search_id is not None:
            where.append("search_id = ?")
            params.append(search_id)
        return where, params

    async def list_cards(
        self,
        *,
        min_score: int | None = None,
        favorite: bool | None = None,
        status: CardStatus | str | None = None,
        search_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Card]:
        where, params = self._card_filters(min_score, favorite, status, search_id)
        sql = "SELECT * FROM cards"
        if where:
            sql += " WHERE " + " AND ".join(where)
        # vacancy_id — вторичный ключ: стабилизирует пагинацию при равных created_at
        sql += " ORDER BY created_at DESC, vacancy_id DESC LIMIT ? OFFSET ?"
        async with self._conn.execute(sql, (*params, limit, offset)) as cur:
            rows = await cur.fetchall()
        return [_row_to_card(r) for r in rows]

    async def count_cards(
        self,
        *,
        min_score: int | None = None,
        favorite: bool | None = None,
        status: CardStatus | str | None = None,
        search_id: int | None = None,
    ) -> int:
        where, params = self._card_filters(min_score, favorite, status, search_id)
        sql = "SELECT COUNT(*) AS n FROM cards"
        if where:
            sql += " WHERE " + " AND ".join(where)
        async with self._conn.execute(sql, params) as cur:
            row = await cur.fetchone()
        return row["n"] if row else 0

    async def mark_card_applied(self, vacancy_id: str) -> None:
        await self._write(
            "UPDATE cards SET status = ?, applied_at = ? WHERE vacancy_id = ?",
            (CardStatus.applied.value, _now_iso(), vacancy_id),
        )

    async def mark_card_skipped(self, vacancy_id: str) -> None:
        await self._write(
            "UPDATE cards SET status = ?, skipped_at = ? WHERE vacancy_id = ?",
            (CardStatus.skipped.value, _now_iso(), vacancy_id),
        )

    async def set_card_favorite(self, vacancy_id: str, fav: bool = True) -> None:
        await self._write(
            "UPDATE cards SET favorite = ? WHERE vacancy_id = ?", (int(fav), vacancy_id)
        )

    # --- системные уведомления (то, что раньше слал send_text) ----------------

    async def add_event(self, text: str, level: str = "info") -> None:
        await self._write(
            "INSERT INTO events (level, text, created_at) VALUES (?, ?, ?)",
            (level, text, _now_iso()),
        )

    async def list_events(self, limit: int = 50, offset: int = 0) -> list[Event]:
        async with self._conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        ) as cur:
            rows = await cur.fetchall()
        return [
            Event(
                id=r["id"],
                level=r["level"],
                text=r["text"],
                created_at=_parse_dt(r["created_at"]),
            )
            for r in rows
        ]
