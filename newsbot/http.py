"""One shared HTTP session for every source."""

from __future__ import annotations

import aiohttp

from newsbot.config import Settings


class FetchError(RuntimeError):
    """A site was unreachable or answered with an error status."""


def build_session(settings: Settings) -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=settings.http_timeout),
        headers={"User-Agent": settings.user_agent, "Accept": "*/*"},
        raise_for_status=False,
    )


async def download(
    session: aiohttp.ClientSession,
    url: str,
    headers: dict[str, str] | None = None,
    label: str | None = None,
) -> bytes:
    who = label or url
    try:
        async with session.get(url, headers=headers or None, allow_redirects=True) as response:
            if response.status >= 400:
                raise FetchError(f"{who}: HTTP {response.status} from {url}")
            return await response.read()
    except aiohttp.ClientError as exc:
        raise FetchError(f"{who}: {exc}") from exc
    except TimeoutError as exc:
        raise FetchError(f"{who}: timed out") from exc
