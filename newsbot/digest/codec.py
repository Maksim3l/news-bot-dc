"""Digest to JSON and back, so a digest built at dawn survives a restart before it is posted."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from newsbot.digest.builder import Digest, DigestEntry
from newsbot.digest.config import DigestConfig
from newsbot.digest.english import EnglishLink
from newsbot.digest.pool import Candidate
from newsbot.digest.quotes import Line, Quote
from newsbot.digest.select import Pick


def dump(digest: Digest) -> str:
    entries = []
    for entry in digest.entries:
        candidate = entry.pick.candidate
        entries.append({
            "category": entry.pick.category.key,
            "reason": entry.pick.reason,
            "candidate": {
                "fingerprint": candidate.fingerprint,
                "source": candidate.source,
                "language": candidate.language,
                "title": candidate.title,
                "url": candidate.url,
                "summary": candidate.summary,
                "published": candidate.published.isoformat() if candidate.published else None,
            },
            "english": (
                {"title": entry.english.title, "url": entry.english.url, "source": entry.english.source}
                if entry.english else None
            ),
        })
    quote = None
    if digest.quote:
        line = digest.quote.line
        quote = {
            "season": line.season, "episode": line.episode, "episode_name": line.episode_name,
            "character": line.character, "text": line.text, "why": digest.quote.why,
        }
    return json.dumps({"day": digest.day.isoformat(), "considered": digest.considered, "entries": entries, "quote": quote})


def load(payload: str, config: DigestConfig) -> Digest:
    """Entries whose category was removed from digest.yml since the build are dropped."""
    raw: dict[str, Any] = json.loads(payload)
    digest = Digest(day=date.fromisoformat(raw["day"]), considered=int(raw["considered"]))
    for entry in raw["entries"]:
        category = config.category(entry["category"])
        if category is None:
            continue
        c = entry["candidate"]
        candidate = Candidate(
            fingerprint=c["fingerprint"], source=c["source"], language=c["language"], title=c["title"],
            url=c["url"], summary=c["summary"],
            published=datetime.fromisoformat(c["published"]) if c["published"] else None,
        )
        english = EnglishLink(**entry["english"]) if entry["english"] else None
        digest.entries.append(DigestEntry(pick=Pick(category, candidate, entry["reason"]), english=english))
    if raw["quote"]:
        q = raw["quote"]
        line = Line(q["season"], q["episode"], q["episode_name"], q["character"], q["text"])
        digest.quote = Quote(line=line, why=q["why"])
    return digest
