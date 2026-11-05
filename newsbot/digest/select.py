"""Picks at most one article per category from the day's pool."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from newsbot.digest.config import Category, DigestConfig
from newsbot.digest.llm import DigestError, JsonModel
from newsbot.digest.pool import Candidate

log = logging.getLogger(__name__)

NONE = -1

SYSTEM = """You are the editor of a small daily news digest posted to a Discord server.
You get a numbered list of articles collected over the last day, in English and Slovenian.
For each category, pick the single article that best fits its brief, or none.
Rules:
- One article per category at most, and never the same article in two categories.
- Picking none (candidate_id -1) is correct when nothing clears the quality bar. Do not fill a slot with filler.
- Prefer substance over hype: concrete results, numbers, decisions, launches, published research.
- Skip ads, sponsored posts, listicles, rumours, opinion pieces and press releases with no news in them.
- An article marked PREFERRED for a category must win that category if it is genuinely about the subject.
- reason is one plain sentence in English saying why this article, for a reader who has not seen it.
- For a non-English pick, english_query is a short English news search query (4 to 8 words) that would find
  English coverage of the same story. If one of the listed English articles already covers the same story,
  put its number in english_candidate_id, otherwise -1. For English picks use "" and -1.
- quote_keywords: 5 to 10 single English words describing the themes and mood of the chosen articles
  (for finding a matching line from the TV show The Office).
- remembered_quotes: up to 5 lines from The Office (US) that would fit the chosen articles, as close to
  verbatim as you can recall. They are checked against the real transcript, so do not invent lines."""


@dataclass(slots=True)
class Pick:
    category: Category
    candidate: Candidate
    reason: str
    english_query: str = ""
    english_candidate: Candidate | None = None


@dataclass(slots=True)
class Selection:
    picks: list[Pick] = field(default_factory=list)
    quote_keywords: list[str] = field(default_factory=list)
    remembered_quotes: list[str] = field(default_factory=list)


def preferred_for(candidate: Candidate, categories: list[Category]) -> list[str]:
    text = candidate.searchable_text
    return [c.key for c in categories if c.prefer and any(p.search(text) for p in c.prefer)]


def shortlist(candidates: list[Candidate], config: DigestConfig) -> list[Candidate]:
    """Caps the prompt size. Preferred items are always kept, the rest are the newest."""
    preferred = [c for c in candidates if preferred_for(c, config.categories)]
    others = [c for c in candidates if c not in preferred]
    room = max(config.max_candidates - len(preferred), 0)
    return preferred + others[:room]


def build_prompt(candidates: list[Candidate], config: DigestConfig) -> str:
    lines = ["Categories:"]
    for category in config.categories:
        line = f"- {category.key} ({category.title}): {category.brief}"
        if category.prefer_note:
            line += f" PRIORITY: {category.prefer_note}"
        lines.append(line)
    if config.quality_bar:
        lines += ["", f"Quality bar: {config.quality_bar}"]

    lines += ["", f"Articles ({len(candidates)}):"]
    for index, candidate in enumerate(candidates):
        date = candidate.published.date().isoformat() if candidate.published else "undated"
        summary = candidate.summary[: config.summary_chars] if config.summary_chars else ""
        marks = preferred_for(candidate, config.categories)
        flag = f" PREFERRED for {', '.join(marks)}" if marks else ""
        tail = f" | {summary}" if summary else ""
        lines.append(f"[{index}] ({candidate.language}, {candidate.source}, {date}){flag} {candidate.title}{tail}")
    return "\n".join(lines)


def selection_schema(config: DigestConfig) -> dict[str, Any]:
    pick = {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": [c.key for c in config.categories]},
            "candidate_id": {"type": "integer"},
            "reason": {"type": "string"},
            "english_query": {"type": "string"},
            "english_candidate_id": {"type": "integer"},
        },
        "required": ["category", "candidate_id", "reason", "english_query", "english_candidate_id"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "picks": {"type": "array", "items": pick},
            "quote_keywords": {"type": "array", "items": {"type": "string"}},
            "remembered_quotes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["picks", "quote_keywords", "remembered_quotes"],
        "additionalProperties": False,
    }


def _at(candidates: list[Candidate], index: Any) -> Candidate | None:
    if isinstance(index, int) and 0 <= index < len(candidates):
        return candidates[index]
    return None


def parse_selection(raw: dict[str, Any], candidates: list[Candidate], config: DigestConfig) -> Selection:
    selection = Selection(
        quote_keywords=[str(word) for word in raw.get("quote_keywords") or []],
        remembered_quotes=[str(line) for line in raw.get("remembered_quotes") or []],
    )
    used: set[str] = set()
    by_key = {entry.get("category"): entry for entry in raw.get("picks") or []}

    for category in config.categories:
        entry = by_key.get(category.key) or {}
        chosen = _at(candidates, entry.get("candidate_id"))
        reason = str(entry.get("reason") or "").strip()

        forced = _forced_pick(category, candidates, chosen, used)
        if forced is not None and forced is not chosen:
            log.info("digest: %s forced to preferred item %r", category.key, forced.title)
            chosen, reason = forced, reason if chosen is None else ""
            entry = {**entry, "english_candidate_id": NONE, "english_query": ""}

        if chosen is None or chosen.fingerprint in used:
            continue
        used.add(chosen.fingerprint)

        english = _at(candidates, entry.get("english_candidate_id"))
        if english is not None and (english.language != "en" or english.fingerprint == chosen.fingerprint):
            english = None
        selection.picks.append(
            Pick(
                category=category,
                candidate=chosen,
                reason=reason or (category.prefer_note or "Matches this category's priority list."),
                english_query=str(entry.get("english_query") or "").strip() or chosen.title,
                english_candidate=english,
            )
        )
    return selection


def _forced_pick(
    category: Category, candidates: list[Candidate], chosen: Candidate | None, used: set[str]
) -> Candidate | None:
    """A preferred item always wins its category, whatever the model chose. Candidates are newest first."""
    if not category.prefer:
        return None
    preferred = [
        c for c in candidates
        if c.fingerprint not in used and any(p.search(c.searchable_text) for p in category.prefer)
    ]
    if not preferred:
        return None
    if chosen is not None and chosen in preferred:
        return chosen
    return preferred[0]


async def select(model: JsonModel, candidates: list[Candidate], config: DigestConfig) -> tuple[Selection, list[Candidate]]:
    listed = shortlist(candidates, config)
    if not listed:
        return Selection(), listed
    raw = await model.ask(SYSTEM, build_prompt(listed, config), selection_schema(config))
    if not isinstance(raw, dict):
        raise DigestError("selection response was not an object")
    return parse_selection(raw, listed, config), listed
