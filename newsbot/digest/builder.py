"""Runs one digest: pool -> selection -> English links -> quote. Knows nothing about Discord."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime

import aiohttp

from newsbot.digest.config import DigestConfig
from newsbot.digest.english import EnglishLink, find_english
from newsbot.digest.llm import DigestError, JsonModel
from newsbot.digest.pool import CandidatePool
from newsbot.digest.quotes import Quote, QuoteBook, pick_quote
from newsbot.digest.select import Pick, select

log = logging.getLogger(__name__)


@dataclass(slots=True)
class DigestEntry:
    pick: Pick
    english: EnglishLink | None = None


@dataclass(slots=True)
class Digest:
    day: date
    considered: int
    entries: list[DigestEntry] = field(default_factory=list)
    quote: Quote | None = None

    @property
    def empty(self) -> bool:
        return not self.entries


class DigestBuilder:
    def __init__(
        self,
        config: DigestConfig,
        pool: CandidatePool,
        model: JsonModel,
        session: aiohttp.ClientSession,
    ) -> None:
        self.config = config
        self._pool = pool
        self._model = model
        self._session = session
        self._book: QuoteBook | None = None

    def today(self) -> date:
        return datetime.now(self.config.timezone).date()

    async def build(self) -> Digest:
        try:
            return await self._build()
        finally:
            release = getattr(self._model, "release", None)
            if release is not None:
                await release()

    async def _build(self) -> Digest:
        candidates = await asyncio.to_thread(self._pool.recent, self.config.window_hours)
        selection, listed = await select(self._model, candidates, self.config)
        digest = Digest(day=self.today(), considered=len(listed))

        for pick in selection.picks:
            english = None
            try:
                english = await find_english(self._model, self._session, pick, self.config)
            except DigestError as exc:
                log.warning("digest: English match failed for %r (%s)", pick.candidate.title, exc)
            digest.entries.append(DigestEntry(pick=pick, english=english))

        if digest.entries and self.config.quote.enabled:
            try:
                book = await self._quote_book()
                digest.quote = await pick_quote(
                    self._model,
                    book,
                    selection.picks,
                    selection.quote_keywords,
                    selection.remembered_quotes,
                    self.config.quote.shortlist,
                )
            except DigestError as exc:
                log.warning("digest: quote step failed (%s)", exc)
        return digest

    def mark_posted(self, digest: Digest) -> None:
        day = digest.day.isoformat()
        self._pool.mark_picked([entry.pick.candidate.fingerprint for entry in digest.entries], day)
        self._pool.mark_day_posted(day)

    async def _quote_book(self) -> QuoteBook:
        if self._book is None:
            self._book = await asyncio.to_thread(QuoteBook.load, self.config.quote.dataset)
        return self._book
