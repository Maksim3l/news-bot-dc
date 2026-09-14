from __future__ import annotations

import json

import pytest

from newsbot.config import ConfigError, SourceConfig
from newsbot.fetchers import available_kinds, get_fetcher
from newsbot.fetchers._parse import parse_datetime, split_selector
from newsbot.fetchers.html import fetch_html
from newsbot.fetchers.json_api import dig, fetch_json
from newsbot.fetchers.rss import fetch_feed


async def test_rss_entries_become_items(rss_bytes: bytes) -> None:
    source = SourceConfig(name="Slo-Tech", url="https://slo-tech.com/rss")
    items = await fetch_feed(source, rss_bytes)

    assert [item.title for item in items] == ["Nova graficna kartica", "Linux 6.18 izdan"]
    assert items[0].url == "https://slo-tech.com/novice/t111"
    assert items[0].summary == "Kratek opis novice."  # html stripped out of the description
    assert items[0].published is not None
    assert items[0].source == "Slo-Tech"


async def test_html_selectors_extract_and_absolutise(html_bytes: bytes) -> None:
    source = SourceConfig(
        name="Blog",
        url="https://blog.example/news",
        kind="html",
        selectors={
            "item": "article.post",
            "title": "h2 a",
            "link": "h2 a@href",
            "summary": "p.excerpt",
            "image": "img@src",
            "published": "time@datetime",
        },
    )
    items = await fetch_html(source, html_bytes)

    assert len(items) == 2  # article.advert is not selected
    assert items[0].title == "First story"
    assert items[0].url == "https://blog.example/news/one"
    assert items[0].summary == "Summary of the first story."
    assert items[0].image == "https://blog.example/img/one.png"
    assert items[0].published is not None
    assert items[1].url == "https://other.example/two"
    assert items[1].image is None


async def test_html_missing_selectors_do_not_raise(html_bytes: bytes) -> None:
    source = SourceConfig(
        name="Blog",
        url="https://blog.example",
        kind="html",
        selectors={"item": "article.post", "title": "h2 a", "summary": ".does-not-exist"},
    )
    items = await fetch_html(source, html_bytes)
    assert items[0].summary == ""


async def test_non_url_attributes_are_left_alone() -> None:
    page = b"""<html><body><article>
      <a href="/story">Headline</a>
      <meta itemprop="datePublished" content="2025-09-09T08:30:00+00:00"/>
    </article></body></html>"""
    source = SourceConfig(
        name="Blog",
        url="https://blog.example/news",
        kind="html",
        selectors={"item": "article", "title": "a", "link": "a@href", "published": "meta@content"},
    )
    items = await fetch_html(source, page)

    assert items[0].url == "https://blog.example/story"  # href is resolved
    assert items[0].published is not None  # the date is not mangled into a URL


async def test_json_paths() -> None:
    payload = json.dumps(
        {"data": {"posts": [{"headline": "API story", "permalink": "/p/1", "excerpt": "Short", "at": 1757404800}]}}
    ).encode()
    source = SourceConfig(
        name="API",
        url="https://api.example/posts",
        kind="json",
        selectors={"item": "data.posts", "title": "headline", "link": "permalink", "summary": "excerpt", "published": "at"},
    )
    items = await fetch_json(source, payload)

    assert items[0].title == "API story"
    assert items[0].url == "https://api.example/p/1"
    assert items[0].published is not None


def test_dig_walks_lists_and_dicts() -> None:
    payload = {"a": [{"b": "found"}]}
    assert dig(payload, "a.0.b") == "found"
    assert dig(payload, "a.9.b") is None
    assert dig(payload, "missing") is None


@pytest.mark.parametrize(
    ("spec", "expected"),
    [("h2 a@href", ("h2 a", "href")), ("h2", ("h2", None)), ("@href", ("", "href"))],
)
def test_split_selector(spec: str, expected: tuple[str, str | None]) -> None:
    assert split_selector(spec) == expected


@pytest.mark.parametrize(
    "raw",
    ["Tue, 09 Sep 2025 10:00:00 +0200", "2025-09-09T08:30:00+00:00", "2025-09-09", "1757404800"],
)
def test_parse_datetime_accepts_common_formats(raw: str) -> None:
    parsed = parse_datetime(raw)
    assert parsed is not None and parsed.tzinfo is not None


def test_parse_datetime_gives_up_quietly() -> None:
    assert parse_datetime("not a date") is None


def test_registry_exposes_every_kind() -> None:
    assert {"rss", "html", "json"} <= set(available_kinds())
    assert get_fetcher("atom") is fetch_feed
    with pytest.raises(ConfigError, match="unknown source type"):
        get_fetcher("carrier-pigeon")
