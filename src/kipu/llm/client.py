"""Async LLM client: strict JSON-schema answers through OpenRouter, Anthropic as fallback.

Every answer is cached on disk by sha256(model, prompt, schema, input), so a replay makes no
calls and gives the same output. Any failure (after retries) returns None: the caller treats it
as "no candidates from this model" and the job goes on with the rules.
"""

import asyncio
import hashlib
import json
import logging
import os
import random
import threading
from collections import defaultdict
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import anthropic
import openai

from kipu.config import LLMSettings

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1"
MAX_ATTEMPTS = 4
BACKOFF_BASE = 1.0
MAX_OUTPUT_TOKENS = 8192
# USD per 1M tokens (input, output) from the OpenRouter catalog on 2026-10-31; used only when
# the provider does not return the cost (Anthropic fallback).
PRICES = {
    "anthropic/claude-haiku-5.5": (0.10, 0.50),
    "google/gemini-3.5-flash-lite": (0.30, 2.50),
    "anthropic/claude-sonnet-5.5": (2.00, 10.00),
}
RETRYABLE = (
    openai.RateLimitError,
    openai.InternalServerError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    anthropic.APITimeoutError,
    anthropic.APIConnectionError,
    TimeoutError,
)


class BadAnswer(Exception):
    """The provider answered, but not with a JSON object (truncated, refused, malformed)."""


@dataclass
class Answer:
    data: dict[str, Any]
    tokens_in: int
    tokens_out: int
    usd: float


Backend = Callable[[str, str, str, dict[str, Any], str], Awaitable[Answer]]


@dataclass
class Usage:
    calls: int = 0
    cache_hits: int = 0
    failures: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    usd: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "usd": round(self.usd, 6)}


class ProviderBackend:
    """OpenRouter first; for anthropic/* models the Anthropic API when OpenRouter fails."""

    def __init__(self, settings: LLMSettings) -> None:
        self.openrouter = (
            openai.AsyncOpenAI(
                api_key=settings.openrouter_key,
                base_url=OPENROUTER_URL,
                timeout=settings.timeout,
                max_retries=0,
            )
            if settings.openrouter_key
            else None
        )
        self.anthropic = (
            anthropic.AsyncAnthropic(
                api_key=settings.anthropic_key, timeout=settings.timeout, max_retries=0
            )
            if settings.anthropic_key
            else None
        )

    def providers(self, model: str) -> list[Backend]:
        found: list[Backend] = []
        if self.openrouter:
            found.append(self._openrouter)
        if self.anthropic and model.startswith("anthropic/"):
            found.append(self._anthropic)
        return found

    async def _openrouter(
        self, model: str, system: str, user: str, schema: dict[str, Any], name: str
    ) -> Answer:
        assert self.openrouter is not None
        response = await self.openrouter.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": name, "strict": True, "schema": schema},
            },
            max_tokens=MAX_OUTPUT_TOKENS,
            # Low reasoning effort: same answers on our prompts at about 1/6 of the tokens.
            extra_body={
                "provider": {"require_parameters": True},
                "usage": {"include": True},
                "reasoning": {"effort": "low"},
            },
        )
        choice = response.choices[0] if response.choices else None
        if choice is None or choice.finish_reason == "length" or not choice.message.content:
            raise BadAnswer(f"no complete answer (finish={getattr(choice, 'finish_reason', '')})")
        usage = response.usage
        tokens_in = usage.prompt_tokens if usage else 0
        tokens_out = usage.completion_tokens if usage else 0
        cost = (usage.model_extra or {}).get("cost") if usage else None
        usd = float(cost) if cost is not None else _price(model, tokens_in, tokens_out)
        return Answer(_json_object(choice.message.content), tokens_in, tokens_out, usd)

    async def _anthropic(
        self, model: str, system: str, user: str, schema: dict[str, Any], name: str
    ) -> Answer:
        assert self.anthropic is not None
        response = await self.anthropic.messages.create(
            model=anthropic_model_id(model),
            max_tokens=MAX_OUTPUT_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": schema}, "effort": "low"},
        )
        if response.stop_reason in ("max_tokens", "refusal"):
            raise BadAnswer(f"stop_reason={response.stop_reason}")
        text = next((b.text for b in response.content if b.type == "text"), "")
        tokens_in, tokens_out = response.usage.input_tokens, response.usage.output_tokens
        return Answer(
            _json_object(text), tokens_in, tokens_out, _price(model, tokens_in, tokens_out)
        )


def anthropic_model_id(slug: str) -> str:
    """`anthropic/claude-haiku-5.5` → `claude-haiku-5-5`."""
    return slug.removeprefix("anthropic/").replace(".", "-")


def _price(model: str, tokens_in: int, tokens_out: int) -> float:
    price_in, price_out = PRICES.get(model, (0.0, 0.0))
    return (tokens_in * price_in + tokens_out * price_out) / 1_000_000


def _json_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BadAnswer("answer is not JSON") from exc
    if not isinstance(value, dict):
        raise BadAnswer("answer is not a JSON object")
    return value


_BACKENDS: dict[tuple[str, str, float], ProviderBackend] = {}
_BACKENDS_LOCK = threading.Lock()


def shared_backend(settings: LLMSettings) -> ProviderBackend:
    """One backend (and connection pool) per process and key pair."""
    key = (settings.openrouter_key, settings.anthropic_key, settings.timeout)
    with _BACKENDS_LOCK:
        if key not in _BACKENDS:
            _BACKENDS[key] = ProviderBackend(settings)
        return _BACKENDS[key]


class LLMClient:
    """Per-job client: cache, concurrency limit, retries, usage and the cost cap."""

    def __init__(
        self,
        settings: LLMSettings,
        cache_dir: Path,
        providers: Callable[[str], list[Backend]] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self.cache_dir = cache_dir
        self.providers = providers or shared_backend(settings).providers
        self.sleep = sleep
        self.usage: dict[str, Usage] = defaultdict(Usage)
        self.capped = False
        self._semaphore: asyncio.Semaphore | None = None

    @property
    def usd(self) -> float:
        return sum(u.usd for u in self.usage.values())

    def totals(self) -> dict[str, Any]:
        return {
            "tokens_in": sum(u.tokens_in for u in self.usage.values()),
            "tokens_out": sum(u.tokens_out for u in self.usage.values()),
            "usd": round(self.usd, 6),
            "models": {m: u.as_dict() for m, u in sorted(self.usage.items())},
        }

    async def complete(
        self, model: str, system: str, user: str, schema: dict[str, Any], name: str
    ) -> dict[str, Any] | None:
        key = cache_key(model, system, user, schema)
        cached = self._cache_get(key)
        if cached is not None:
            self.usage[model].cache_hits += 1
            return cached
        if self._over_budget():
            return None
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.settings.concurrency)
        async with self._semaphore:
            if self._over_budget():
                return None
            answer = await self._call(model, system, user, schema, name)
        if answer is None:
            self.usage[model].failures += 1
            return None
        usage = self.usage[model]
        usage.calls += 1
        usage.tokens_in += answer.tokens_in
        usage.tokens_out += answer.tokens_out
        usage.usd += answer.usd
        self._cache_put(key, model, answer.data)
        return answer.data

    async def _call(
        self, model: str, system: str, user: str, schema: dict[str, Any], name: str
    ) -> Answer | None:
        for provider in self.providers(model):
            for attempt in range(MAX_ATTEMPTS):
                try:
                    return await asyncio.wait_for(
                        provider(model, system, user, schema, name), self.settings.timeout + 5
                    )
                except (*RETRYABLE, BadAnswer) as exc:
                    log.warning("%s attempt %d failed: %s", model, attempt + 1, type(exc).__name__)
                    if attempt + 1 < MAX_ATTEMPTS:
                        await self.sleep(BACKOFF_BASE * 2**attempt + random.uniform(0, 0.5))
                except (openai.APIError, anthropic.APIError) as exc:
                    log.warning("%s failed without retry: %s", model, type(exc).__name__)
                    break
        return None

    def _over_budget(self) -> bool:
        if self.usd >= self.settings.max_cost_usd:
            if not self.capped:
                log.warning(
                    "LLM cost cap of %.2f USD reached: no more calls, rules only from here",
                    self.settings.max_cost_usd,
                )
            self.capped = True
        return self.capped

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / key[:2] / f"{key}.json"

    def _cache_get(self, key: str) -> dict[str, Any] | None:
        path = self._cache_path(key)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))["data"]
        except (json.JSONDecodeError, KeyError, OSError):
            return None

    def _cache_put(self, key: str, model: str, data: dict[str, Any]) -> None:
        path = self._cache_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"model": model, "data": data}, ensure_ascii=False), "utf-8")
        os.replace(tmp, path)


def cache_key(model: str, system: str, user: str, schema: dict[str, Any]) -> str:
    payload = "\x1f".join((model, system, json.dumps(schema, sort_keys=True), user))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class LoopThread:
    """A process-wide event loop in a daemon thread, so sync code (the pipeline, FastAPI sync
    routes) can run batches of async calls on one long-lived connection pool."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True, name="kipu-llm").start()

    def run[T](self, coro: Coroutine[Any, Any, T]) -> T:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()


_LOOP: LoopThread | None = None
_LOOP_LOCK = threading.Lock()


def loop_thread() -> LoopThread:
    global _LOOP
    with _LOOP_LOCK:
        if _LOOP is None:
            _LOOP = LoopThread()
        return _LOOP
