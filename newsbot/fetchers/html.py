"""Sites without a feed. Driven entirely by CSS selectors from sources.yml."""

from __future__ import annotations

from bs4 import BeautifulSoup
from bs4.element import Tag

from newsbot.config import SourceConfig
from newsbot.fetchers import register
from newsbot.fetchers._parse import absolute_url, parse_datetime, shorten, split_selector, strip_html
from newsbot.models import Item

SUMMARY_LIMIT = 400
LINK_ATTRIBUTES = {"href", "src", "data-src", "data-lazy-src", "srcset"}


@register("html")
@register("scrape")
async def fetch_html(source: SourceConfig, body: bytes) -> list[Item]:
    soup = BeautifulSoup(body, "lxml")
    item_selector = source.selectors.get("item") or "article"
    return [item for element in soup.select(item_selector) if (item := _element_to_item(source, element))]


def _element_to_item(source: SourceConfig, element: Tag) -> Item | None:
    link = extract(element, source.selectors.get("link", "a@href"), source.url)
    title = extract(element, source.selectors.get("title", "a"), source.url)
    if not title and not link:
        return None

    image = extract(element, source.selectors.get("image", ""), source.url)
    return Item(
        source=source.name,
        title=shorten(title or link, 250),
        url=link,
        summary=shorten(extract(element, source.selectors.get("summary", ""), source.url), SUMMARY_LIMIT),
        image=image or None,
        author=extract(element, source.selectors.get("author", ""), source.url) or None,
        published=parse_datetime(extract(element, source.selectors.get("published", ""), source.url)),
    )


def extract(element: Tag, spec: str, base_url: str) -> str:
    """Reads one field. 'div.lead' takes text, 'a@href' takes an attribute, '@href' reads the item itself."""
    if not spec:
        return ""

    selector, attribute = split_selector(spec)
    target = element if not selector else element.select_one(selector)
    if target is None:
        return ""

    if not attribute:
        return strip_html(target.decode_contents())

    value = target.get(attribute)
    if isinstance(value, list):
        value = " ".join(value)
    if not value:
        return ""
    if attribute == "srcset":
        value = value.split(",")[0].strip().split(" ")[0]
    if attribute in LINK_ATTRIBUTES:
        return absolute_url(value, base_url)
    return str(value).strip()
