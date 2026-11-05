from __future__ import annotations

import pytest

from newsbot import poller as poller_module
from newsbot.config import FilterRules, Settings, SourceConfig
from newsbot.http import FetchError
from newsbot.models import Item
from newsbot.poller import Poller
from newsbot.store import SeenStore


class Recorder:
    def __init__(self) -> None:
        self.published: list[tuple[str, list[Item]]] = []

    async def __call__(self, source: SourceConfig, items: list[Item]) -> None:
        self.published.append((source.name, items))


@pytest.fixture
def source() -> SourceConfig:
    return SourceConfig(name="Slo-Tech", url="https://slo-tech.com/rss", channel_id=1, max_items=5)


class Site:
    """Stands in for the network: the body it serves can change between polls."""

    def __init__(self, body: bytes, error: Exception | None = None) -> None:
        self.body = body
        self.error = error

    async def download(self, session, url, headers=None, label=None) -> bytes:
        if self.error is not None:
            raise self.error
        return self.body


@pytest.fixture
def build(settings: Settings, rss_bytes: bytes, monkeypatch: pytest.MonkeyPatch):
    def factory(body: bytes | None = None, error: Exception | None = None):
        site = Site(body if body is not None else rss_bytes, error)
        monkeypatch.setattr(poller_module, "download", site.download)
        recorder = Recorder()
        store = SeenStore(settings.database_path)
        return Poller(settings, store, session=None, publish=recorder), recorder, store, site

    return factory


async def test_first_poll_seeds_without_posting(build, source: SourceConfig) -> None:
    poller, recorder, store, _ = build()

    assert await poller.poll_once(source) == []
    assert recorder.published == []
    assert store.knows_source("Slo-Tech") is True


async def test_items_arriving_after_the_first_poll_get_posted(build, source: SourceConfig, rss_bytes: bytes) -> None:
    poller, recorder, _, site = build()
    await poller.poll_once(source)

    assert await poller.poll_once(source) == []  # same feed contents, nothing to say

    site.body = rss_bytes.replace(
        b"<item>",
        b"""<item>
        <title>Brand new</title>
        <link>https://slo-tech.com/novice/t333</link>
        <pubDate>Wed, 10 Sep 2025 10:00:00 +0200</pubDate>
        </item><item>""",
        1,
    )
    posted = await poller.poll_once(source)

    assert [item.title for item in posted] == ["Brand new"]
    assert recorder.published == [("Slo-Tech", posted)]


async def test_announce_backlog_posts_the_whole_first_batch(build, settings: Settings, source: SourceConfig) -> None:
    settings.announce_backlog = True
    poller, recorder, _, _ = build()

    posted = await poller.poll_once(source)
    assert len(posted) == 2
    assert recorder.published[0][1] == posted


async def test_batch_is_capped_and_the_rest_wait(build, settings: Settings, source: SourceConfig) -> None:
    settings.announce_backlog = True
    source.max_items = 1
    poller, _, _, _ = build()

    first = await poller.poll_once(source)
    second = await poller.poll_once(source)

    assert len(first) == 1 and len(second) == 1
    assert first[0].title != second[0].title
    assert first[0].published < second[0].published  # oldest first, so the channel reads chronologically


async def test_filters_apply_before_dedupe(build, settings: Settings, source: SourceConfig) -> None:
    import re

    settings.announce_backlog = True
    source.filters = FilterRules(include=(re.compile("linux", re.I),))
    poller, _, _, _ = build()

    posted = await poller.poll_once(source)
    assert [item.title for item in posted] == ["Linux 6.18 izdan"]


async def test_fetch_error_is_recorded_not_swallowed(build, source: SourceConfig) -> None:
    poller, _, _, _ = build(error=FetchError("Slo-Tech: HTTP 503"))

    with pytest.raises(FetchError):
        await poller.poll_once(source)

    poller.record_error(source.name, "Slo-Tech: HTTP 503")
    assert poller.status["Slo-Tech"].last_error == "Slo-Tech: HTTP 503"


def test_backoff_grows_with_failures(build, source: SourceConfig) -> None:
    poller, _, _, _ = build()
    source.interval = 100

    assert poller._next_delay(source, failures=1) == 200
    assert poller._next_delay(source, failures=2) == 400
    assert poller._next_delay(source, failures=20) == poller_module.MAX_BACKOFF
    assert 90 <= poller._next_delay(source, failures=0) <= 110


async def test_collector_sees_every_fetched_item(settings: Settings, rss_bytes: bytes, monkeypatch, source: SourceConfig) -> None:
    site = Site(rss_bytes)
    monkeypatch.setattr(poller_module, "download", site.download)
    collected: list[Item] = []

    async def collect(src: SourceConfig, items: list[Item]) -> None:
        collected.extend(items)

    poller = Poller(settings, SeenStore(settings.database_path), None, Recorder(), collect)
    await poller.poll_once(source)
    assert len(collected) == 2


async def test_digest_only_source_never_posts(build, settings: Settings, source: SourceConfig) -> None:
    settings.announce_backlog = True
    source.post = False
    poller, recorder, store, _ = build()

    assert await poller.poll_once(source) == []
    assert recorder.published == []
    assert store.count() == 0
