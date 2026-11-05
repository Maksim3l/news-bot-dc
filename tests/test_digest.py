from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from newsbot.config import ConfigError, Settings
from newsbot.digest import english as english_module
from newsbot.digest.builder import DigestBuilder
from newsbot.digest.config import Category, DigestConfig, load_digest
from newsbot.digest.pool import CandidatePool
from newsbot.digest.quotes import Line, QuoteBook
from newsbot.digest.render import build_digest_embeds, digest_text
from newsbot.digest.scheduler import seconds_until
from newsbot.digest.select import build_prompt, parse_selection, shortlist
from newsbot.models import Item

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
NOW = datetime.now(timezone.utc)


class FakeModel:
    """Answers each ask() with the next canned reply and records the prompts it was given."""

    def __init__(self, *replies: dict[str, Any]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def ask(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        return self.replies.pop(0)


def make_config(**overrides: Any) -> DigestConfig:
    values: dict[str, Any] = dict(
        enabled=True,
        channel_id=1,
        post_at=time(8, 0),
        timezone=ZoneInfo("Europe/Ljubljana"),
        model="claude-haiku-5-5",
        effort="medium",
        refusal_fallback=False,
        window_hours=24,
        max_age_hours=48,
        max_candidates=400,
        keep_days=14,
        post_when_empty=False,
        quality_bar="",
        categories=[
            Category("tech", "Tech", "tech news", prefer=(re.compile("dewe ?soft", re.I),), prefer_note="Dewesoft wins"),
            Category("good_news", "Good news", "climate and good news"),
        ],
    )
    values.update(overrides)
    return DigestConfig(**values)


def item(source: str, title: str, hours_ago: float = 1, summary: str = "") -> Item:
    return Item(
        source=source,
        title=title,
        url=f"https://example.com/{abs(hash(title))}",
        summary=summary,
        published=NOW - timedelta(hours=hours_ago),
    )


@pytest.fixture
def pool(tmp_path: Path) -> CandidatePool:
    return CandidatePool(tmp_path / "seen.sqlite3")


def fill(pool: CandidatePool) -> None:
    pool.add([item("Slo-Tech", "Nova baterija podvoji domet", 3)], "sl", 48)
    pool.add(
        [
            item("Hacker News", "New battery doubles EV range", 2),
            item("Positive News", "Wolves return to the Alps", 1),
            item("Ars", "Old story", 100),
        ],
        "en",
        48,
    )


# --- pool -------------------------------------------------------------------


def test_pool_skips_old_items_and_duplicates(pool: CandidatePool) -> None:
    fill(pool)
    assert pool.add([item("Hacker News", "New battery doubles EV range", 2)], "en", 48) == 0
    titles = [c.title for c in pool.recent(24)]
    assert "Old story" not in titles
    assert titles[0] == "Wolves return to the Alps"  # newest first


def test_picked_items_never_come_back(pool: CandidatePool) -> None:
    fill(pool)
    wolves = next(c for c in pool.recent(24) if "Wolves" in c.title)
    pool.mark_picked([wolves.fingerprint], "2026-11-05")
    assert all("Wolves" not in c.title for c in pool.recent(24))


def test_run_is_recorded_once_per_day(pool: CandidatePool) -> None:
    assert not pool.has_run("2026-11-05")
    pool.record_run("2026-11-05")
    assert pool.has_run("2026-11-05")


# --- selection ----------------------------------------------------------------


def test_prompt_lists_language_source_and_preferred_flag(pool: CandidatePool) -> None:
    pool.add([item("GNews", "Dewesoft opens a new plant", 1)], "en", 48)
    prompt = build_prompt(pool.recent(24), make_config())
    assert "(en, GNews," in prompt
    assert "PREFERRED for tech" in prompt
    assert "PRIORITY: Dewesoft wins" in prompt


def test_parse_selection_maps_ids_and_respects_none(pool: CandidatePool) -> None:
    fill(pool)
    candidates = pool.recent(24)
    battery_sl = next(i for i, c in enumerate(candidates) if c.language == "sl")
    battery_en = next(i for i, c in enumerate(candidates) if c.title.startswith("New battery"))
    raw = {
        "picks": [
            {"category": "tech", "candidate_id": battery_sl, "reason": "Doubles range.", "english_query": "battery range", "english_candidate_id": battery_en},
            {"category": "good_news", "candidate_id": -1, "reason": "", "english_query": "", "english_candidate_id": -1},
        ],
        "quote_keywords": ["battery"],
        "remembered_quotes": [],
    }
    selection = parse_selection(raw, candidates, make_config())

    assert [p.category.key for p in selection.picks] == ["tech"]
    assert selection.picks[0].candidate.language == "sl"
    assert selection.picks[0].english_candidate.title == "New battery doubles EV range"


def test_same_article_cannot_fill_two_slots(pool: CandidatePool) -> None:
    fill(pool)
    candidates = pool.recent(24)
    raw = {
        "picks": [
            {"category": "tech", "candidate_id": 0, "reason": "a", "english_query": "", "english_candidate_id": -1},
            {"category": "good_news", "candidate_id": 0, "reason": "b", "english_query": "", "english_candidate_id": -1},
        ],
        "quote_keywords": [],
        "remembered_quotes": [],
    }
    assert len(parse_selection(raw, candidates, make_config()).picks) == 1


def test_preferred_item_wins_even_if_the_model_chose_otherwise(pool: CandidatePool) -> None:
    fill(pool)
    pool.add([item("Dewesoft news (sl)", "DEWESoft predstavil nov sistem", 5)], "sl", 48)
    candidates = pool.recent(24)
    other = next(i for i, c in enumerate(candidates) if c.title.startswith("New battery"))
    raw = {
        "picks": [{"category": "tech", "candidate_id": other, "reason": "battery", "english_query": "x", "english_candidate_id": -1}],
        "quote_keywords": [],
        "remembered_quotes": [],
    }
    pick = parse_selection(raw, candidates, make_config()).picks[0]

    assert pick.candidate.title == "DEWESoft predstavil nov sistem"
    assert pick.reason == "Dewesoft wins"


def test_shortlist_keeps_preferred_items_past_the_cap(pool: CandidatePool) -> None:
    fill(pool)
    pool.add([item("GNews", "Dewesoft news", 40)], "en", 48)
    listed = shortlist(pool.recent(24 * 3), make_config(max_candidates=1))
    assert [c.title for c in listed] == ["Dewesoft news"]


# --- quotes -------------------------------------------------------------------


@pytest.fixture
def book() -> QuoteBook:
    return QuoteBook([
        Line(3, 21, "Product Recall", "Dwight", "Identity theft is not a joke, Jim! Millions of families suffer every year!"),
        Line(4, 1, "Fun Run", "Michael", "I'm not superstitious, but... I'm... I am a little-stitious."),
        Line(5, 2, "Example", "Pam", "The batteries in the remote are dead again."),
    ])


def test_remembered_quote_resolves_to_the_real_line(book: QuoteBook) -> None:
    line = book.verify("Identity theft is not a joke, Jim!")
    assert line is not None and line.character == "Dwight"
    assert book.verify("Global warming is my favourite holiday") is None


def test_search_matches_plurals(book: QuoteBook) -> None:
    assert book.search(["battery"], 5)[0].character == "Pam"


def test_shipped_dataset_loads() -> None:
    book = QuoteBook.load(REPOSITORY_ROOT / "assets" / "office_quotes.csv")
    assert len(book.lines) > 20000
    assert book.verify("Bears. Beets. Battlestar Galactica.") is not None


# --- end to end -----------------------------------------------------------------


async def test_build_runs_selection_english_search_and_quote(
    pool: CandidatePool, book: QuoteBook, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pool.add([item("Slo-Tech", "Nova baterija podvoji domet", 3)], "sl", 48)
    pool.add([item("Positive News", "Wolves return to the Alps", 1)], "en", 48)
    candidates = pool.recent(24)
    sl = next(i for i, c in enumerate(candidates) if c.language == "sl")
    en = next(i for i, c in enumerate(candidates) if c.language == "en")

    search_feed = b"""<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>Unrelated - Reuters</title><link>https://reuters.example/a</link></item>
      <item><title>Slovenian battery doubles range - Electrek</title><link>https://electrek.example/b</link></item>
    </channel></rss>"""
    searched: list[str] = []

    async def fake_download(session, url, headers=None, label=None) -> bytes:
        searched.append(url)
        return search_feed

    monkeypatch.setattr(english_module, "download", fake_download)
    model = FakeModel(
        {
            "picks": [
                {"category": "tech", "candidate_id": sl, "reason": "Range doubles.", "english_query": "Slovenian battery range", "english_candidate_id": -1},
                {"category": "good_news", "candidate_id": en, "reason": "Wolves are back.", "english_query": "", "english_candidate_id": -1},
            ],
            "quote_keywords": ["battery"],
            "remembered_quotes": ["Identity theft is not a joke, Jim!"],
        },
        {"match": 1},
        {"line": 0, "why": "Because."},
    )
    builder = DigestBuilder(make_config(), pool, model, session=None)
    builder._book = book

    digest = await builder.build()

    assert [e.pick.category.key for e in digest.entries] == ["tech", "good_news"]
    assert "Slovenian+battery+range" in searched[0]
    assert digest.entries[0].english.url == "https://electrek.example/b"
    assert digest.entries[0].english.source == "Electrek"
    assert digest.entries[1].english is None  # English pick needs no counterpart
    assert digest.quote.line.character == "Dwight"  # verified remembered line comes first
    assert len(build_digest_embeds(digest)) == 3
    assert "Wolves return to the Alps" in digest_text(digest)

    builder.mark_posted(digest)
    assert pool.recent(24) == []
    assert pool.has_run(digest.day.isoformat())


async def test_empty_pool_makes_no_model_call(pool: CandidatePool) -> None:
    model = FakeModel()
    digest = await DigestBuilder(make_config(), pool, model, session=None).build()
    assert digest.empty and model.prompts == []


# --- config and schedule --------------------------------------------------------


def test_digest_template_parses(settings: Settings) -> None:
    settings.sources_file = REPOSITORY_ROOT / "sources.example.yml"
    settings.digest_file = REPOSITORY_ROOT / "digest.example.yml"
    config = load_digest(settings)

    assert [c.key for c in config.categories] == ["tech", "good_news"]
    assert config.category("tech").prefer[0].search("New from DEWESoft")
    assert config.refusal_fallback is False  # Haiku 5.5 rejects fallbacks
    assert config.channel_id == settings.default_channel_id


def test_digest_rejects_category_without_brief(settings: Settings) -> None:
    settings.sources_file = REPOSITORY_ROOT / "sources.example.yml"
    settings.digest_file.write_text("categories:\n  - key: tech\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="brief"):
        load_digest(settings)


def test_missing_mounted_file_explains_the_directory_case(settings: Settings) -> None:
    settings.digest_file.mkdir()
    settings.sources_file = REPOSITORY_ROOT / "sources.example.yml"
    with pytest.raises(ConfigError, match="is a directory"):
        load_digest(settings)


def test_seconds_until_rolls_over_to_tomorrow() -> None:
    config = make_config()
    zone = config.timezone
    assert seconds_until(config, datetime(2026, 11, 5, 7, 0, tzinfo=zone)) == 3600
    assert seconds_until(config, datetime(2026, 11, 5, 8, 0, tzinfo=zone)) == 24 * 3600
    # Clocks go forward overnight on 29 March 2026 in Ljubljana: 08:00 is 23 real hours away.
    assert seconds_until(config, datetime(2026, 3, 28, 8, 0, tzinfo=zone)) == 23 * 3600
