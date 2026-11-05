"""The only module that talks to the Claude API. Everything else asks for JSON through JsonModel."""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

import anthropic

from newsbot.digest.config import DigestConfig

log = logging.getLogger(__name__)

MAX_TOKENS = 16000
# "default" lets the API pick the fallback model by refusal category, so no model list to maintain.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class DigestError(RuntimeError):
    """The digest could not be built: API failure, refusal, or output that does not fit the schema."""


class JsonModel(Protocol):
    async def ask(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]: ...


class ClaudeJsonModel:
    def __init__(self, config: DigestConfig, client: anthropic.AsyncAnthropic | None = None) -> None:
        self._config = config
        # Reads ANTHROPIC_API_KEY from the environment.
        self._client = client or anthropic.AsyncAnthropic()

    async def ask(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": self._config.model,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {
                "effort": self._config.effort,
                "format": {"type": "json_schema", "schema": schema},
            },
        }
        if self._config.refusal_fallback:
            request["betas"] = [FALLBACK_BETA]
            request["fallbacks"] = "default"

        try:
            response = await self._client.beta.messages.create(**request)
        except anthropic.AuthenticationError as exc:
            raise DigestError("Anthropic rejected the API key. Check ANTHROPIC_API_KEY in .env.") from exc
        except anthropic.BadRequestError as exc:
            raise DigestError(f"Anthropic rejected the request: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            raise DigestError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise DigestError(f"could not reach the Anthropic API: {exc}") from exc

        if response.stop_reason == "refusal":
            raise DigestError("the model declined this request")
        if response.stop_reason == "max_tokens":
            raise DigestError("the model ran out of output tokens before finishing")

        text = "".join(block.text for block in response.content if block.type == "text")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise DigestError(f"model returned invalid JSON: {text[:200]!r}") from exc
