"""Rebuilds assets/office_quotes.csv from the schrute transcript (MIT, Brad Lindblad).

Only needed if you want a different length window. Run once, commit the CSV:
    pip install pyreadr
    python tools/build_office_quotes.py path/to/theoffice.rda

Source file: https://raw.githubusercontent.com/bradlindblad/schrute/master/data/theoffice.rda
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pyreadr

OUTPUT = Path(__file__).resolve().parent.parent / "assets" / "office_quotes.csv"
# Shorter lines are filler ("Okay.", "What?"), longer ones are monologue fragments that read badly alone.
MIN_WORDS = 6
MAX_WORDS = 45


def main(rda_path: str) -> int:
    frame = next(iter(pyreadr.read_r(rda_path).values()))
    rows = []
    for record in frame.itertuples(index=False):
        text = " ".join(str(record.text).split())
        if MIN_WORDS <= len(text.split()) <= MAX_WORDS:
            rows.append((int(record.season), int(record.episode), record.episode_name, record.character, text))

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["season", "episode", "episode_name", "character", "text"])
        writer.writerows(rows)
    print(f"wrote {len(rows)} lines to {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "theoffice.rda"))
