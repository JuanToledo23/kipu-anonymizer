"""Mocked LLM providers for the LLM layer tests: no test reaches the network."""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from kipu.config import LLMSettings
from kipu.llm.client import Answer, LLMClient, loop_thread

PRIMARY, SECOND, STRONG = "p/primary", "s/second", "x/strong"
LLM = LLMSettings(
    enabled=True, primary=PRIMARY, second=SECOND, strong=STRONG, openrouter_key="k", concurrency=1
)


def mention(item: int, text: str, label: str = "PERSON", **extra: str) -> dict[str, Any]:
    return {
        "item": item,
        "text": text,
        "label": label,
        "gender": extra.get("gender", "unknown"),
        "role": extra.get("role", "unknown"),
        "left_context": extra.get("left", ""),
        "right_context": extra.get("right", ""),
    }


class FakeProviders:
    """Answers per model from a function of the user prompt; records every call."""

    def __init__(self, answers: dict[str, Callable[[str], dict[str, Any]]], delay: float = 0):
        self.answers = answers
        self.delay = delay
        self.calls: list[str] = []

    def __call__(self, model: str) -> list:
        async def backend(model: str, system: str, user: str, schema: Any, name: str) -> Answer:
            self.calls.append(model)
            if self.delay:
                await asyncio.sleep(self.delay)
            return Answer(self.answers[model](user), 100, 10, 0.01)

        return [backend]


def client(tmp_path: Path, fake: FakeProviders, settings: LLMSettings = LLM) -> LLMClient:
    return LLMClient(settings, tmp_path / "cache", providers=fake, sleep=no_sleep)


async def no_sleep(_: float) -> None:
    return None


def run(coro: Any) -> Any:
    return loop_thread().run(coro)


def extraction(user: str) -> dict[str, Any]:
    found = []
    for i, line in enumerate(line for line in user.splitlines() if line.startswith("[")):
        if "Yeferson Mosquera" in line:
            found.append(mention(i, "Yeferson Mosquera", gender="m", role="third_party"))
        if "52 años" in line:
            found.append(mention(i, "52 años", "AGE"))
    return {"mentions": found}
