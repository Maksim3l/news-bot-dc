"""Turns a URL a human pasted into a source entry for sources.yml."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urlparse

import aiohttp
import feedparser

from newsbot.config import ConfigError
from newsbot.discovery import candidate_feed_urls, discover_feed_url, guess_title, looks_like_feed
from newsbot.http import FetchError, download


@dataclass(slots=True)
class Discovered:
    name: str
    url: str
    kind: str = "rss"


async def discover_source(session: aiohttp.ClientSession, page_url: str, name: str | None = None) -> Discovered:
    body = await download(session, page_url, label="discovery")

    if looks_like_feed(body):
        return Discovered(name=name or await _feed_title(body, page_url), url=page_url)

    declared = discover_feed_url(body, page_url)
    if declared:
        feed_body = await download(session, declared, label="discovery")
        if looks_like_feed(feed_body):
            return Discovered(name=name or guess_title(body, _hostname(page_url)), url=declared)

    site_name = name or guess_title(body, _hostname(page_url))
    for candidate in candidate_feed_urls(page_url):
        try:
            candidate_body = await download(session, candidate, label="discovery")
        except FetchError:
            continue
        if looks_like_feed(candidate_body):
            return Discovered(name=site_name, url=candidate)

    raise ConfigError(
        f"no feed found for {page_url}. Add it to sources.yml as an `html` source with selectors "
        "(see the README section 'Sites without a feed')."
    )


async def _feed_title(body: bytes, fallback_url: str) -> str:
    parsed = await asyncio.to_thread(feedparser.parse, body)
    title = (parsed.feed or {}).get("title")
    return str(title).strip() if title else _hostname(fallback_url)


def _hostname(url: str) -> str:
    host = urlparse(url).hostname or url
    return host.removeprefix("www.")
