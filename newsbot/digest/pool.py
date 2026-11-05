"""Every fetched item lands here so the daily digest can choose from the whole day, not one feed."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from newsbot.models import Item

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    fingerprint  TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    language     TEXT NOT NULL,
    title        TEXT NOT NULL,
    url          TEXT NOT NULL,
    summary      TEXT NOT NULL,
    published    TEXT,
    collected_at TEXT NOT NULL,
    picked_on    TEXT
);
CREATE INDEX IF NOT EXISTS candidates_collected_idx ON candidates (collected_at);
CREATE TABLE IF NOT EXISTS digest_days (
    run_date  TEXT PRIMARY KEY,
    built_at  TEXT,
    posted_at TEXT,
    payload   TEXT
);
"""


@dataclass(slots=True)
class Candidate:
    fingerprint: str
    source: str
    language: str
    title: str
    url: str
    summary: str
    published: datetime | None

    @property
    def searchable_text(self) -> str:
        return f"{self.title}\n{self.summary}"


class CandidatePool:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.executescript(SCHEMA)
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def add(self, items: list[Item], language: str, max_age_hours: int) -> int:
        """Stores items not seen before. Old items are skipped so a new feed's archive never competes."""
        now = datetime.now(timezone.utc)
        oldest = now - timedelta(hours=max_age_hours)
        rows = [
            (
                item.fingerprint,
                item.source,
                language,
                item.title,
                item.url,
                item.summary,
                item.published.isoformat() if item.published else None,
                now.isoformat(),
            )
            for item in items
            if item.published is None or item.published >= oldest
        ]
        cursor = self._connection.executemany(
            "INSERT OR IGNORE INTO candidates "
            "(fingerprint, source, language, title, url, summary, published, collected_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._connection.commit()
        return cursor.rowcount

    def recent(self, window_hours: int) -> list[Candidate]:
        """Unpicked items collected inside the window, newest first."""
        since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
        rows = self._connection.execute(
            "SELECT fingerprint, source, language, title, url, summary, published FROM candidates "
            "WHERE collected_at >= ? AND picked_on IS NULL "
            "AND url NOT IN (SELECT url FROM candidates WHERE picked_on IS NOT NULL AND url != '') "
            "ORDER BY COALESCE(published, collected_at) DESC",
            (since,),
        ).fetchall()
        return [
            Candidate(
                fingerprint=row[0],
                source=row[1],
                language=row[2],
                title=row[3],
                url=row[4],
                summary=row[5],
                published=datetime.fromisoformat(row[6]) if row[6] else None,
            )
            for row in rows
        ]

    def mark_picked(self, fingerprints: list[str], run_date: str) -> None:
        self._connection.executemany(
            "UPDATE candidates SET picked_on = ? WHERE fingerprint = ?",
            [(run_date, fingerprint) for fingerprint in fingerprints],
        )
        self._connection.commit()

    def save_built(self, run_date: str, payload: str) -> None:
        """Keeps the built digest on disk so a restart before post time does not lose it."""
        self._connection.execute(
            "INSERT INTO digest_days (run_date, built_at, payload) VALUES (?, ?, ?) "
            "ON CONFLICT(run_date) DO UPDATE SET built_at = excluded.built_at, payload = excluded.payload",
            (run_date, datetime.now(timezone.utc).isoformat(), payload),
        )
        self._connection.commit()

    def built_payload(self, run_date: str) -> str | None:
        row = self._connection.execute(
            "SELECT payload FROM digest_days WHERE run_date = ? AND built_at IS NOT NULL", (run_date,)
        ).fetchone()
        return row[0] if row else None

    def mark_day_posted(self, run_date: str) -> None:
        self._connection.execute(
            "INSERT INTO digest_days (run_date, posted_at) VALUES (?, ?) "
            "ON CONFLICT(run_date) DO UPDATE SET posted_at = excluded.posted_at",
            (run_date, datetime.now(timezone.utc).isoformat()),
        )
        self._connection.commit()

    def is_posted(self, run_date: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM digest_days WHERE run_date = ? AND posted_at IS NOT NULL", (run_date,)
        ).fetchone()
        return row is not None

    def count(self) -> int:
        return self._connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]

    def prune(self, keep_days: int) -> int:
        """Picked rows are kept so the same story is never picked twice; the rest expire."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()
        cursor = self._connection.execute(
            "DELETE FROM candidates WHERE collected_at < ? AND picked_on IS NULL", (cutoff,)
        )
        self._connection.commit()
        return cursor.rowcount
