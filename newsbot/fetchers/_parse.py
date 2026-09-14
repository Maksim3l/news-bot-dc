"""Helpers shared by the fetchers: text cleanup, date coercion, URL resolution."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

_WHITESPACE = re.compile(r"\s+")

ISO_FORMATS = ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    text = BeautifulSoup(value, "lxml").get_text(" ", strip=True)
    return _WHITESPACE.sub(" ", text).strip()


def shorten(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def absolute_url(value: str | None, base_url: str) -> str:
    if not value:
        return ""
    return urljoin(base_url, value.strip())


def parse_datetime(value: str | None) -> datetime | None:
    """Accepts RFC 822 (feeds), ISO 8601 (html time tags) and unix seconds."""
    if not value:
        return None
    text = str(value).strip()

    if text.isdigit():
        return datetime.fromtimestamp(int(text), tz=timezone.utc)

    try:
        parsed = parsedate_to_datetime(text)
        if parsed is not None:
            return _as_utc(parsed)
    except (TypeError, ValueError):
        pass

    try:
        return _as_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        pass

    for fmt in ISO_FORMATS:
        try:
            return _as_utc(datetime.strptime(text, fmt))
        except ValueError:
            continue
    return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def split_selector(spec: str) -> tuple[str, str | None]:
    """'h2 a@href' -> ('h2 a', 'href'); 'h2' -> ('h2', None); '@href' -> ('', 'href')."""
    selector, separator, attribute = spec.rpartition("@")
    if not separator:
        return spec.strip(), None
    return selector.strip(), attribute.strip()
