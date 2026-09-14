"""Remembers what has already been posted so a restart does not repeat the whole feed."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from newsbot.models import Item

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    fingerprint TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    url         TEXT NOT NULL,
    title       TEXT NOT NULL,
    seen_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS seen_source_idx ON seen (source);
"""


class SeenStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.executescript(SCHEMA)
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def new_items(self, items: list[Item]) -> list[Item]:
        fresh: list[Item] = []
        batch_keys: set[str] = set()
        for item in items:
            key = item.fingerprint
            if key in batch_keys or self._is_seen(key):
                continue
            batch_keys.add(key)
            fresh.append(item)
        return fresh

    def mark_seen(self, items: list[Item]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._connection.executemany(
            "INSERT OR IGNORE INTO seen (fingerprint, source, url, title, seen_at) VALUES (?, ?, ?, ?, ?)",
            [(item.fingerprint, item.source, item.url, item.title, now) for item in items],
        )
        self._connection.commit()

    def knows_source(self, source_name: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM seen WHERE source = ? LIMIT 1", (source_name,)
        ).fetchone()
        return row is not None

    def count(self, source_name: str | None = None) -> int:
        if source_name is None:
            return self._connection.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
        return self._connection.execute(
            "SELECT COUNT(*) FROM seen WHERE source = ?", (source_name,)
        ).fetchone()[0]

    def forget_source(self, source_name: str) -> int:
        cursor = self._connection.execute("DELETE FROM seen WHERE source = ?", (source_name,))
        self._connection.commit()
        return cursor.rowcount

    def prune(self, retention_days: int) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        cursor = self._connection.execute("DELETE FROM seen WHERE seen_at < ?", (cutoff,))
        self._connection.commit()
        return cursor.rowcount

    def _is_seen(self, fingerprint: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM seen WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
        return row is not None
