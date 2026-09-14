from __future__ import annotations

import pytest

from newsbot.config import ConfigError
from newsbot.discovery import candidate_feed_urls, discover_feed_url, guess_title, looks_like_feed
from newsbot.http import FetchError
from newsbot.subscribe import discover_source

HOMEPAGE = b"""<html><head>
  <title>Slo-Tech | Novice</title>
  <meta property="og:site_name" content="Slo-Tech"/>
  <link rel="alternate" type="application/rss+xml" href="/rss"/>
</head><body></body></html>"""

PLAIN_PAGE = b"<html><head><title>Plain Blog</title></head><body></body></html>"
FEED = b'<?xml version="1.0"?><rss version="2.0"><channel><title>Slo-Tech</title></channel></rss>'


class FakeWeb:
    """Serves canned bodies by URL; anything unmapped behaves like a 404."""

    def __init__(self, routes: dict[str, bytes]) -> None:
        self.routes = routes
        self.requested: list[str] = []

    async def download(self, session, url, headers=None, label=None) -> bytes:
        self.requested.append(url)
        if url not in self.routes:
            raise FetchError(f"{url}: HTTP 404")
        return self.routes[url]


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch):
    def install(routes: dict[str, bytes]) -> FakeWeb:
        fake = FakeWeb(routes)
        monkeypatch.setattr("newsbot.subscribe.download", fake.download)
        return fake

    return install


def test_looks_like_feed() -> None:
    assert looks_like_feed(FEED) is True
    assert looks_like_feed(HOMEPAGE) is False


def test_discover_feed_url_reads_the_link_tag() -> None:
    assert discover_feed_url(HOMEPAGE, "https://slo-tech.com/") == "https://slo-tech.com/rss"
    assert discover_feed_url(PLAIN_PAGE, "https://blog.example/") is None


def test_guess_title_prefers_og_site_name() -> None:
    assert guess_title(HOMEPAGE, "fallback") == "Slo-Tech"
    assert guess_title(PLAIN_PAGE, "fallback") == "Plain Blog"


def test_candidate_paths_are_absolute() -> None:
    assert "https://blog.example/feed" in candidate_feed_urls("https://blog.example/news")


async def test_a_feed_url_is_used_as_is(web) -> None:
    web({"https://slo-tech.com/rss": FEED})
    found = await discover_source(None, "https://slo-tech.com/rss")

    assert found.url == "https://slo-tech.com/rss"
    assert found.name == "Slo-Tech"  # taken from the feed's own title
    assert found.kind == "rss"


async def test_a_homepage_resolves_through_its_link_tag(web) -> None:
    web({"https://slo-tech.com/": HOMEPAGE, "https://slo-tech.com/rss": FEED})
    found = await discover_source(None, "https://slo-tech.com/")

    assert found.url == "https://slo-tech.com/rss"
    assert found.name == "Slo-Tech"


async def test_common_paths_are_probed_when_nothing_is_declared(web) -> None:
    fake = web({"https://blog.example/": PLAIN_PAGE, "https://blog.example/feed": FEED})
    found = await discover_source(None, "https://blog.example/")

    assert found.url == "https://blog.example/feed"
    assert found.name == "Plain Blog"
    assert "https://blog.example/rss" in fake.requested  # tried before /feed


async def test_an_explicit_name_wins(web) -> None:
    web({"https://slo-tech.com/rss": FEED})
    found = await discover_source(None, "https://slo-tech.com/rss", name="Moje novice")
    assert found.name == "Moje novice"


async def test_a_site_without_a_feed_says_what_to_do(web) -> None:
    web({"https://blog.example/": PLAIN_PAGE})
    with pytest.raises(ConfigError, match="html"):
        await discover_source(None, "https://blog.example/")
