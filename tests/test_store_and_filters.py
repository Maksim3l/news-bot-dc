from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from newsbot.config import FilterRules
from newsbot.filters import apply_filters, wanted
from newsbot.models import Item
from newsbot.store import SeenStore


def make_item(title: str, url: str, source: str = "Slo-Tech", summary: str = "") -> Item:
    return Item(source=source, title=title, url=url, summary=summary)


def test_fingerprint_is_stable_and_source_scoped() -> None:
    first = make_item("A", "https://x.example/1")
    same = make_item("Different title", "https://x.example/1")
    other_source = make_item("A", "https://x.example/1", source="Other")

    assert first.fingerprint == same.fingerprint  # the URL identifies the story
    assert first.fingerprint != other_source.fingerprint


def test_store_filters_out_seen_items(tmp_path: Path) -> None:
    store = SeenStore(tmp_path / "seen.sqlite3")
    items = [make_item("A", "https://x.example/1"), make_item("B", "https://x.example/2")]

    assert store.new_items(items) == items
    store.mark_seen(items)
    assert store.new_items(items) == []
    assert store.new_items(items + [make_item("C", "https://x.example/3")])[0].title == "C"


def test_store_drops_duplicates_inside_one_batch(tmp_path: Path) -> None:
    store = SeenStore(tmp_path / "seen.sqlite3")
    twice = [make_item("A", "https://x.example/1"), make_item("A", "https://x.example/1")]
    assert len(store.new_items(twice)) == 1


def test_knows_source_drives_first_run_detection(tmp_path: Path) -> None:
    store = SeenStore(tmp_path / "seen.sqlite3")
    assert store.knows_source("Slo-Tech") is False
    store.mark_seen([make_item("A", "https://x.example/1")])
    assert store.knows_source("Slo-Tech") is True


def test_forget_source_clears_only_that_source(tmp_path: Path) -> None:
    store = SeenStore(tmp_path / "seen.sqlite3")
    store.mark_seen([make_item("A", "https://x.example/1"), make_item("B", "https://y.example/1", source="Other")])

    assert store.forget_source("Slo-Tech") == 1
    assert store.count() == 1
    assert store.knows_source("Other") is True


def test_prune_removes_old_rows(tmp_path: Path) -> None:
    path = tmp_path / "seen.sqlite3"
    store = SeenStore(path)
    store.mark_seen([make_item("A", "https://x.example/1")])

    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    with sqlite3.connect(path) as raw:
        raw.execute("UPDATE seen SET seen_at = ?", (old,))

    assert store.prune(90) == 1
    assert store.count() == 0


def test_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "seen.sqlite3"
    first = SeenStore(path)
    first.mark_seen([make_item("A", "https://x.example/1")])
    first.close()

    assert SeenStore(path).new_items([make_item("A", "https://x.example/1")]) == []


def test_include_acts_as_allowlist() -> None:
    rules = FilterRules(include=(re.compile("linux", re.I),))
    assert wanted(make_item("Linux 6.18", "u"), rules) is True
    assert wanted(make_item("Windows 12", "u"), rules) is False


def test_exclude_wins_over_include() -> None:
    rules = FilterRules(include=(re.compile("linux", re.I),), exclude=(re.compile("oglas", re.I),))
    assert wanted(make_item("Linux oglas", "u"), rules) is False


def test_filters_read_the_summary_too() -> None:
    rules = FilterRules(include=(re.compile("nvidia", re.I),))
    assert wanted(make_item("Neka novica", "u", summary="Gonilniki NVIDIA"), rules) is True


def test_no_rules_lets_everything_through() -> None:
    items = [make_item("A", "u1"), make_item("B", "u2")]
    assert apply_filters(items, FilterRules()) == items
