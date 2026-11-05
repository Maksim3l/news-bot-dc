"""Picks a line from the real Office transcript. The model only chooses; it never writes the quote."""

from __future__ import annotations

import csv
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from newsbot.digest.config import ConfigError
from newsbot.digest.llm import JsonModel
from newsbot.digest.select import Pick

WORD = re.compile(r"[a-z']+")
STOPWORDS = frozenset(
    "a an the and or but if so to of in on at by for with from as is are was were be been being it its this that "
    "these those i you he she we they me him her us them my your his our their what which who whom how why when "
    "where there here do does did done have has had not no yes just like know get got go going oh okay ok well "
    "um uh hey all can will would should could im youre dont thats its".split()
)
# Share of a remembered quote's content words that must appear in one transcript line.
# Lower and paraphrases start matching unrelated lines.
MATCH_SHARE = 0.8
MIN_MATCH_WORDS = 3

SYSTEM = """You pick the quote of the day for a news digest: one line from The Office (US) that fits today's
articles, ideally funny in context. You only choose from the numbered lines given; never write your own.
Return the number of the best line, and one short sentence (why) connecting it to the news.
Prefer lines that stand on their own without the scene around them."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"line": {"type": "integer"}, "why": {"type": "string"}},
    "required": ["line", "why"],
    "additionalProperties": False,
}


@dataclass(slots=True, frozen=True)
class Line:
    season: int
    episode: int
    episode_name: str
    character: str
    text: str

    @property
    def credit(self) -> str:
        return f"{self.character}, S{self.season}E{self.episode} \"{self.episode_name}\""


@dataclass(slots=True)
class Quote:
    line: Line
    why: str


def words(text: str) -> list[str]:
    return [w for w in WORD.findall(text.lower().replace("’", "'")) if w not in STOPWORDS and len(w) > 2]


class QuoteBook:
    def __init__(self, lines: list[Line]) -> None:
        self.lines = lines
        self._index: dict[str, set[int]] = defaultdict(set)
        for position, line in enumerate(lines):
            for word in set(words(line.text)):
                self._index[word].add(position)
        total = max(len(lines), 1)
        self._idf = {word: math.log(total / len(hits)) for word, hits in self._index.items()}

    @classmethod
    def load(cls, path: Path) -> "QuoteBook":
        if not path.exists():
            raise ConfigError(f"quote dataset {path} not found; see tools/build_office_quotes.py")
        with path.open(encoding="utf-8", newline="") as handle:
            lines = [
                Line(int(row["season"]), int(row["episode"]), row["episode_name"], row["character"], row["text"])
                for row in csv.DictReader(handle)
            ]
        return cls(lines)

    def search(self, keywords: list[str], limit: int) -> list[Line]:
        """Lines sharing the most informative words with the keywords. Prefix match catches plurals."""
        scores: Counter[int] = Counter()
        for keyword in {w for k in keywords for w in words(k)}:
            stem = keyword[:5] if len(keyword) > 5 else keyword
            for word, hits in self._index.items():
                if word == keyword or (len(stem) >= 5 and word.startswith(stem)):
                    for position in hits:
                        scores[position] += self._idf[word]
        return [self.lines[position] for position, _ in scores.most_common(limit)]

    def verify(self, remembered: str) -> Line | None:
        """Returns the real line a remembered quote comes from, or None if it is not in the transcript.

        Remembered quotes are usually an excerpt of a longer line, so this checks word containment,
        not whole-string similarity.
        """
        query = set(words(remembered))
        if len(query) < MIN_MATCH_WORDS:
            return None
        rare = sorted(query, key=lambda w: self._idf.get(w, 0), reverse=True)[:3]
        pool: set[int] = set()
        for word in rare:
            pool |= self._index.get(word, set())
        best, best_key = None, (0.0, 0)
        for position in pool:
            line = self.lines[position]
            share = len(query & set(words(line.text))) / len(query)
            key = (share, -len(line.text))
            if key > best_key:
                best, best_key = line, key
        return best if best_key[0] >= MATCH_SHARE else None

def shortlist(book: QuoteBook, keywords: list[str], remembered: list[str], limit: int) -> list[Line]:
    verified = [line for text in remembered if (line := book.verify(text))]
    found = book.search(keywords, limit)
    merged: list[Line] = []
    for line in verified + found:
        if line not in merged:
            merged.append(line)
    return merged[:limit]


def _prompt(picks: list[Pick], lines: list[Line]) -> str:
    out = ["Today's articles:"]
    out += [f"- {pick.candidate.title} | {pick.reason}" for pick in picks]
    out += ["", "Lines:"]
    out += [f"[{index}] {line.character}: {line.text}" for index, line in enumerate(lines)]
    return "\n".join(out)


async def pick_quote(
    model: JsonModel, book: QuoteBook, picks: list[Pick], keywords: list[str], remembered: list[str], limit: int
) -> Quote | None:
    lines = shortlist(book, keywords, remembered, limit)
    if not lines:
        return None
    raw = await model.ask(SYSTEM, _prompt(picks, lines), SCHEMA)
    index = raw.get("line") if isinstance(raw, dict) else None
    if not isinstance(index, int) or not 0 <= index < len(lines):
        return None
    return Quote(line=lines[index], why=str(raw.get("why") or "").strip())
