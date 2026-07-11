"""SQLite-хранилище состояния агента (реализация StorageProto)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import aiosqlite

from ..models import Application, ScoreResult, SearchQuery

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
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


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
