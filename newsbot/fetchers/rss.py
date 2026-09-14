"""RSS and Atom feeds. The default source type."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import feedparser

from newsbot.config import SourceConfig
from newsbot.fetchers import register
from newsbot.fetchers._parse import absolute_url, shorten, strip_html
from newsbot.models import Item

SUMMARY_LIMIT = 400


@register("rss")
@register("atom")
@register("feed")
async def fetch_feed(source: SourceConfig, body: bytes) -> list[Item]:
    # feedparser is blocking and can be slow on large feeds, so it never runs on the event loop.
    parsed = await asyncio.to_thread(feedparser.parse, body)
    return [item for entry in parsed.entries if (item := _entry_to_item(source, entry))]


def _entry_to_item(source: SourceConfig, entry: Any) -> Item | None:
    url = absolute_url(entry.get("link"), source.url)
    title = strip_html(entry.get("title")) or url
    if not url and not title:
        return None

    return Item(
        source=source.name,
        title=shorten(title, 250),
        url=url,
        summary=shorten(strip_html(entry.get("summary") or entry.get("description")), SUMMARY_LIMIT),
        image=_entry_image(entry),
        author=entry.get("author") or None,
        published=_entry_published(entry),
    )


def _entry_image(entry: Any) -> str | None:
    for media in entry.get("media_content") or []:
        if media.get("url"):
            return media["url"]
    for thumb in entry.get("media_thumbnail") or []:
        if thumb.get("url"):
            return thumb["url"]
    for enclosure in entry.get("enclosures") or []:
        if str(enclosure.get("type", "")).startswith("image/"):
            return enclosure.get("href")
    return None


def _entry_published(entry: Any) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        stamp = entry.get(key)
        if stamp:
            return datetime(*stamp[:6], tzinfo=timezone.utc)
    return None
