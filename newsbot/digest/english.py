"""Finds English coverage of a story picked from a non-English source."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus

import aiohttp

from newsbot.config import SourceConfig
from newsbot.digest.config import DigestConfig
from newsbot.digest.llm import JsonModel
from newsbot.digest.select import Pick
from newsbot.fetchers import get_fetcher
from newsbot.http import FetchError, download
from newsbot.models import Item

log = logging.getLogger(__name__)

SYSTEM = """You match news stories across languages. You get one article (often Slovenian) and a numbered
list of English search results. Return the number of the English result that reports the same specific
story or event, or -1 if none does. Same topic is not enough: it must be the same news."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"match": {"type": "integer"}},
    "required": ["match"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class EnglishLink:
    title: str
    url: str
    source: str


async def search_english(session: aiohttp.ClientSession, query: str, config: DigestConfig) -> list[Item]:
    url = config.english.search_url.format(query=quote_plus(query))
    search = SourceConfig(name="English search", url=url)
    body = await download(session, url, label="English search")
    items = await get_fetcher("rss")(search, body)
    return items[: config.english.results]


def _prompt(pick: Pick, results: list[Item]) -> str:
    story = pick.candidate
    lines = [f"Article ({story.language}, {story.source}): {story.title} | {story.summary[:400]}", "", "English results:"]
    lines += [f"[{index}] {item.title}" for index, item in enumerate(results)]
    return "\n".join(lines)


async def find_english(
    model: JsonModel, session: aiohttp.ClientSession, pick: Pick, config: DigestConfig
) -> EnglishLink | None:
    if pick.candidate.language == "en" or not config.english.enabled:
        return None
    if pick.english_candidate is not None:
        found = pick.english_candidate
        return EnglishLink(title=found.title, url=found.url, source=found.source)

    try:
        results = await search_english(session, pick.english_query or pick.candidate.title, config)
    except FetchError as exc:
        log.warning("digest: English search failed for %r (%s)", pick.english_query, exc)
        return None
    if not results:
        return None

    raw = await model.ask(SYSTEM, _prompt(pick, results), SCHEMA)
    index = raw.get("match") if isinstance(raw, dict) else None
    if not isinstance(index, int) or not 0 <= index < len(results):
        return None
    match = results[index]
    title, _, publisher = match.title.rpartition(" - ")  # Google News titles end in " - Publisher"
    return EnglishLink(title=title or match.title, url=match.url, source=publisher or "Google News")
