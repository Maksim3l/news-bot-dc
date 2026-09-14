"""The single shape every fetcher produces and every sink consumes."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class Item:
    source: str
    title: str
    url: str
    summary: str = ""
    image: str | None = None
    author: str | None = None
    published: datetime | None = None

    @property
    def fingerprint(self) -> str:
        """Dedupe key. Changing this makes the bot re-post everything it has already seen."""
        raw = f"{self.source}\x00{self.url or self.title}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @property
    def searchable_text(self) -> str:
        return f"{self.title}\n{self.summary}"
