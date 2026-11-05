"""The only module that talks to a model. Everything else asks for JSON through JsonModel."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Protocol

import aiohttp

from newsbot.digest.config import DigestConfig

if TYPE_CHECKING:
    import anthropic

log = logging.getLogger(__name__)

MAX_TOKENS = 16000
# "default" lets the API pick the fallback model by refusal category, so no model list to maintain.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# The model stays in RAM between the calls of one build, then release() unloads it.
OLLAMA_KEEP_ALIVE = "10m"
OLLAMA_PULL_TIMEOUT = 4 * 3600


class DigestError(RuntimeError):
    """The digest could not be built: model failure, refusal, or output that does not fit the schema."""


class JsonModel(Protocol):
    async def ask(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]: ...


def _parse(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DigestError(f"model returned invalid JSON: {text[:200]!r}") from exc
    if not isinstance(value, dict):
        raise DigestError("model returned JSON that is not an object")
    return value


class OllamaJsonModel:
    """Local model through Ollama's /api/chat with the JSON schema as the output format."""

    def __init__(self, config: DigestConfig, session: aiohttp.ClientSession) -> None:
        self._config = config
        self._settings = config.ollama
        self._session = session

    async def ask(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        options: dict[str, Any] = {"temperature": 0, "num_ctx": self._settings.num_ctx}
        if self._settings.num_thread:
            options["num_thread"] = self._settings.num_thread
        body: dict[str, Any] = {
            "model": self._config.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "format": schema,
            "stream": False,
            "keep_alive": OLLAMA_KEEP_ALIVE,
            "options": options,
        }
        if self._settings.think is not None:
            body["think"] = self._settings.think

        reply = await self._post("/api/chat", body, self._settings.request_timeout)
        if reply.get("done_reason") == "length":
            raise DigestError("the model ran out of context or output tokens; raise ollama.num_ctx")
        _log_speed(reply)
        return _parse(str((reply.get("message") or {}).get("content") or ""))

    async def ensure_model(self) -> None:
        """Downloads the model once if Ollama does not have it. Safe to call on every start."""
        tags = await self._get("/api/tags")
        names = {model.get("name") for model in tags.get("models") or []}
        wanted = self._config.model if ":" in self._config.model else f"{self._config.model}:latest"
        if wanted in names:
            return
        if not self._settings.pull_missing:
            raise DigestError(f"Ollama has no model {wanted!r}; run: ollama pull {wanted}")
        log.info("downloading %s into Ollama, this happens once and can take a while", wanted)
        await self._post("/api/pull", {"model": wanted, "stream": False}, OLLAMA_PULL_TIMEOUT)
        log.info("%s is ready", wanted)

    async def release(self) -> None:
        """Unloads the model so its RAM is free outside the build window."""
        try:
            await self._post("/api/generate", {"model": self._config.model, "keep_alive": 0}, 60)
        except DigestError as exc:
            log.warning("could not unload %s (%s)", self._config.model, exc)

    async def _get(self, path: str) -> dict[str, Any]:
        url = self._settings.url + path
        try:
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                return await self._json(response, url)
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise DigestError(f"cannot reach Ollama at {self._settings.url} ({exc or type(exc).__name__})") from exc

    async def _post(self, path: str, body: dict[str, Any], timeout: int) -> dict[str, Any]:
        url = self._settings.url + path
        try:
            async with self._session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=timeout)) as response:
                return await self._json(response, url)
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise DigestError(f"cannot reach Ollama at {self._settings.url} ({exc or type(exc).__name__})") from exc

    @staticmethod
    async def _json(response: aiohttp.ClientResponse, url: str) -> dict[str, Any]:
        text = await response.text()
        if response.status >= 400:
            raise DigestError(f"Ollama answered {response.status} for {url}: {text[:300]}")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise DigestError(f"Ollama sent non-JSON from {url}: {text[:200]!r}") from exc


def _log_speed(reply: dict[str, Any]) -> None:
    prompt_tokens, prompt_ns = reply.get("prompt_eval_count") or 0, reply.get("prompt_eval_duration") or 0
    output_tokens, output_ns = reply.get("eval_count") or 0, reply.get("eval_duration") or 0
    if prompt_ns and output_ns:
        log.info(
            "ollama: read %d tokens at %.0f tok/s, wrote %d at %.1f tok/s",
            prompt_tokens, prompt_tokens / (prompt_ns / 1e9), output_tokens, output_tokens / (output_ns / 1e9),
        )


class ClaudeJsonModel:
    def __init__(self, config: DigestConfig, client: "anthropic.AsyncAnthropic | None" = None) -> None:
        import anthropic

        self._anthropic = anthropic
        self._config = config
        # Reads ANTHROPIC_API_KEY from the environment.
        self._client = client or anthropic.AsyncAnthropic()

    async def ask(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        anthropic = self._anthropic
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
        return _parse("".join(block.text for block in response.content if block.type == "text"))


def make_model(config: DigestConfig, session: aiohttp.ClientSession) -> JsonModel:
    if config.provider == "anthropic":
        return ClaudeJsonModel(config)
    return OllamaJsonModel(config, session)
