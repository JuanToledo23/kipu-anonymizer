"""LLM client: cache, retries, degradation and the cost cap (mocked providers)."""

from pathlib import Path
from typing import Any

from tests.llm_fakes import LLM, PRIMARY, SECOND, FakeProviders, client, no_sleep, run

from kipu.config import LLMSettings
from kipu.llm.client import Answer, LLMClient


def test_cache_hit_makes_no_call(tmp_path: Path) -> None:
    fake = FakeProviders({PRIMARY: lambda u: {"mentions": []}})
    llm = client(tmp_path, fake)
    for _ in range(2):
        assert run(llm.complete(PRIMARY, "sys", "hola", {}, "m")) == {"mentions": []}
    again = client(tmp_path, fake)
    assert run(again.complete(PRIMARY, "sys", "hola", {}, "m")) == {"mentions": []}
    assert fake.calls == [PRIMARY]
    assert llm.usage[PRIMARY].cache_hits == 1 and again.usage[PRIMARY].cache_hits == 1
    assert llm.totals()["usd"] == 0.01


def test_retries_then_degrades_to_no_candidates(tmp_path: Path) -> None:
    attempts: list[int] = []

    def flaky(model: str) -> list:
        async def backend(*_: Any) -> Answer:
            attempts.append(1)
            if len(attempts) < 3:
                raise TimeoutError
            return Answer({"ok": True}, 1, 1, 0.0)

        async def broken(*_: Any) -> Answer:
            raise TimeoutError

        return [broken] if model == SECOND else [backend]

    llm = LLMClient(LLM, tmp_path, providers=flaky, sleep=no_sleep)
    assert run(llm.complete(PRIMARY, "s", "u", {}, "m")) == {"ok": True}
    assert len(attempts) == 3
    assert run(llm.complete(SECOND, "s", "u", {}, "m")) is None
    assert llm.usage[SECOND].failures == 1


def test_cost_cap_stops_calls(tmp_path: Path) -> None:
    fake = FakeProviders({PRIMARY: lambda u: {"mentions": []}})
    capped = LLMSettings(**{**LLM.__dict__, "max_cost_usd": 0.015})
    llm = client(tmp_path, fake, capped)
    results = [run(llm.complete(PRIMARY, "s", f"u{i}", {}, "m")) for i in range(4)]
    assert results[:2] == [{"mentions": []}] * 2 and results[2:] == [None, None]
    assert len(fake.calls) == 2 and llm.capped
