"""Digest to Discord embeds, and to plain text for the command-line dry run."""

from __future__ import annotations

import discord

from newsbot.digest.builder import Digest

QUOTE_COLOUR = discord.Colour(0x8a9ba8)
PICK_COLOURS = (0x2b7fff, 0x2ecc71, 0xe67e22, 0x9b59b6)
DESCRIPTION_LIMIT = 1000


def digest_heading(digest: Digest) -> str:
    return f"**Daily digest, {digest.day.day} {digest.day:%b %Y}**"


def build_digest_embeds(digest: Digest) -> list[discord.Embed]:
    embeds = []
    for index, entry in enumerate(digest.entries):
        pick = entry.pick
        embed = discord.Embed(
            title=pick.candidate.title[:256],
            url=pick.candidate.url or None,
            description=pick.reason[:DESCRIPTION_LIMIT],
            colour=discord.Colour(PICK_COLOURS[index % len(PICK_COLOURS)]),
        )
        embed.set_author(name=pick.category.title[:256])
        if entry.english:
            embed.add_field(
                name="In English",
                value=f"[{entry.english.title[:200]}]({entry.english.url}) ({entry.english.source})"[:1024],
                inline=False,
            )
        embed.set_footer(text=pick.candidate.source)
        embeds.append(embed)

    if digest.quote:
        line = digest.quote.line
        embed = discord.Embed(
            title="Quote of the day",
            description=f"> {line.text}\n\n{line.credit}"[:DESCRIPTION_LIMIT],
            colour=QUOTE_COLOUR,
        )
        if digest.quote.why:
            embed.set_footer(text=digest.quote.why[:2048])
        embeds.append(embed)
    return embeds


def digest_text(digest: Digest) -> str:
    lines = [f"Daily digest {digest.day.isoformat()} ({digest.considered} articles considered)", ""]
    if digest.empty:
        lines.append("Nothing cleared the bar.")
    for entry in digest.entries:
        pick = entry.pick
        lines += [f"[{pick.category.title}] {pick.candidate.title}", f"  {pick.candidate.url}", f"  {pick.reason}"]
        if entry.english:
            lines.append(f"  English: {entry.english.title} ({entry.english.source}) {entry.english.url}")
        lines.append("")
    if digest.quote:
        lines += [f'"{digest.quote.line.text}"', f"  {digest.quote.line.credit}", f"  {digest.quote.why}"]
    return "\n".join(lines)
