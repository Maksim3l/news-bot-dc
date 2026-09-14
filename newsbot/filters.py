"""Keyword gate applied between fetching and posting."""

from __future__ import annotations

from newsbot.config import FilterRules
from newsbot.models import Item


def wanted(item: Item, rules: FilterRules) -> bool:
    text = item.searchable_text
    if any(pattern.search(text) for pattern in rules.exclude):
        return False
    if rules.include:
        return any(pattern.search(text) for pattern in rules.include)
    return True


def apply_filters(items: list[Item], rules: FilterRules) -> list[Item]:
    return [item for item in items if wanted(item, rules)]
