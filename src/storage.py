from __future__ import annotations

import sqlite3
from pathlib import Path

from models import SearchConfig


class SeenStorage:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(db_path)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_searches (
                chat_id TEXT NOT NULL,
                url TEXT NOT NULL,
                source TEXT NOT NULL,
                is_seeded INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, url)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_flats (
                chat_id TEXT NOT NULL,
                dedupe_key TEXT NOT NULL,
                source TEXT NOT NULL,
                external_id TEXT NOT NULL,
                title TEXT NOT NULL,
                link TEXT NOT NULL,
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, dedupe_key)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self._ensure_seen_flats_schema()
        self._connection.commit()

    def _ensure_seen_flats_schema(self) -> None:
        columns = self._connection.execute(
            "PRAGMA table_info(seen_flats)").fetchall()
        primary_key_columns = [column[1]
                               for column in columns if column[5] > 0]
        if primary_key_columns == ["chat_id", "dedupe_key"]:
            return

        self._connection.execute("DROP TABLE IF EXISTS seen_flats")
        self._connection.execute(
            """
            CREATE TABLE seen_flats (
                chat_id TEXT NOT NULL,
                dedupe_key TEXT NOT NULL,
                source TEXT NOT NULL,
                external_id TEXT NOT NULL,
                title TEXT NOT NULL,
                link TEXT NOT NULL,
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, dedupe_key)
            )
            """
        )

    def list_searches(self) -> list[SearchConfig]:
        rows = self._connection.execute(
            "SELECT chat_id, url, source, is_seeded FROM chat_searches ORDER BY created_at ASC"
        ).fetchall()
        return [
            SearchConfig(chat_id=row[0], url=row[1],
                         source=row[2], is_seeded=bool(row[3]))
            for row in rows
        ]

    def replace_searches(self, chat_id: str, searches: list[SearchConfig]) -> None:
        self._connection.execute(
            "DELETE FROM chat_searches WHERE chat_id = ?", (chat_id,))
        self._connection.execute(
            "DELETE FROM seen_flats WHERE chat_id = ?", (chat_id,))
        for search in searches:
            self._connection.execute(
                """
                INSERT INTO chat_searches (chat_id, url, source, is_seeded)
                VALUES (?, ?, ?, ?)
                """,
                (search.chat_id, search.url, search.source, int(search.is_seeded)),
            )
        self._connection.commit()

    def mark_seeded(self, chat_id: str, url: str) -> None:
        self._connection.execute(
            "UPDATE chat_searches SET is_seeded = 1 WHERE chat_id = ? AND url = ?",
            (chat_id, url),
        )
        self._connection.commit()

    def get_offset(self) -> int:
        row = self._connection.execute(
            "SELECT value FROM bot_state WHERE key = 'telegram_update_offset'"
        ).fetchone()
        return int(row[0]) if row else 0

    def set_offset(self, offset: int) -> None:
        self._connection.execute(
            """
            INSERT INTO bot_state (key, value) VALUES ('telegram_update_offset', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(offset),),
        )
        self._connection.commit()

    def has(self, chat_id: str, dedupe_key: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM seen_flats WHERE chat_id = ? AND dedupe_key = ? LIMIT 1",
            (chat_id, dedupe_key),
        ).fetchone()
        return row is not None

    def add(
        self,
        chat_id: str,
        dedupe_key: str,
        source: str,
        external_id: str,
        title: str,
        link: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT OR IGNORE INTO seen_flats (chat_id, dedupe_key, source, external_id, title, link)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (chat_id, dedupe_key, source, external_id, title, link),
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()
