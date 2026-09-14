"""JSON endpoints. Selectors are dotted paths into the payload."""

from __future__ import annotations

import json
from typing import Any

from newsbot.config import SourceConfig
from newsbot.fetchers import register
from newsbot.fetchers._parse import absolute_url, parse_datetime, shorten, strip_html
from newsbot.models import Item

SUMMARY_LIMIT = 400


@register("json")
async def fetch_json(source: SourceConfig, body: bytes) -> list[Item]:
    payload = json.loads(body.decode("utf-8", errors="replace"))
    entries = dig(payload, source.selectors.get("item", "")) if source.selectors.get("item") else payload
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return []
    return [item for entry in entries if isinstance(entry, dict) and (item := _entry_to_item(source, entry))]


def _entry_to_item(source: SourceConfig, entry: dict[str, Any]) -> Item | None:
    title = strip_html(_text(dig(entry, source.selectors.get("title", "title"))))
    link = absolute_url(_text(dig(entry, source.selectors.get("link", "url"))), source.url)
    if not title and not link:
        return None

    return Item(
        source=source.name,
        title=shorten(title or link, 250),
        url=link,
        summary=shorten(strip_html(_text(dig(entry, source.selectors.get("summary", "")))), SUMMARY_LIMIT),
        image=_text(dig(entry, source.selectors.get("image", ""))) or None,
        author=_text(dig(entry, source.selectors.get("author", ""))) or None,
        published=parse_datetime(_text(dig(entry, source.selectors.get("published", "")))),
    )


def dig(payload: Any, path: str) -> Any:
    """Walks 'data.articles.0.title' through nested dicts and lists."""
    if not path:
        return None
    current = payload
    for key in path.split("."):
        if current is None:
            return None
        if isinstance(current, list):
            if not key.isdigit() or int(key) >= len(current):
                return None
            current = current[int(key)]
        elif isinstance(current, dict):
            current = current.get(key)
        else:
            return None
    return current


def _text(value: Any) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value)
