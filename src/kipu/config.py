"""Settings from the environment (and `.env` when present). Secrets are never logged."""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

log = logging.getLogger(__name__)

# Chosen from GET https://openrouter.ai/api/v1/models on 2026-10-31 (see DECISIONS.md).
DEFAULT_PRIMARY = "anthropic/claude-haiku-5.5"
DEFAULT_SECOND = "google/gemini-3.5-flash-lite"
DEFAULT_STRONG = "anthropic/claude-sonnet-5.5"


@dataclass(frozen=True)
class LLMSettings:
    """Disabled unless built by `load_settings` with a provider key (tests never call out)."""

    enabled: bool = False
    primary: str = DEFAULT_PRIMARY
    second: str = DEFAULT_SECOND
    strong: str = DEFAULT_STRONG
    openrouter_key: str = field(default="", repr=False)
    anthropic_key: str = field(default="", repr=False)
    max_cost_usd: float = 5.0
    concurrency: int = 12
    timeout: float = 60.0
    anonymize_budget: float = 20.0

    @property
    def models(self) -> list[str]:
        return [self.primary, self.second, self.strong] if self.enabled else []


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    pseudonym_secret: str
    fuentes_url: str
    fuentes_token: str
    entrypoint_token: str
    port: int
    llm: LLMSettings = field(default_factory=LLMSettings)

    @property
    def registry_path(self) -> Path:
        return self.data_dir / "registry.sqlite"

    @property
    def output_dir(self) -> Path:
        return self.data_dir / "output"

    @property
    def llm_cache_dir(self) -> Path:
        return self.data_dir / "llm_cache"


def _env(name: str, default: str = "") -> str:
    # `docker --env-file` keeps trailing spaces that python-dotenv strips; both must agree.
    return os.environ.get(name, default).strip()


def load_llm_settings() -> LLMSettings:
    openrouter_key = _env("OPENROUTER_API_KEY")
    anthropic_key = _env("ANTHROPIC_API_KEY")
    wanted = _env("LLM_ENABLED", "true").lower() not in ("0", "false", "no", "off")
    if wanted and not (openrouter_key or anthropic_key):
        log.warning("LLM_ENABLED but no OPENROUTER_API_KEY/ANTHROPIC_API_KEY: rules only")
    return LLMSettings(
        enabled=wanted and bool(openrouter_key or anthropic_key),
        primary=_env("LLM_PRIMARY") or DEFAULT_PRIMARY,
        second=_env("LLM_SECOND") or DEFAULT_SECOND,
        strong=_env("LLM_STRONG") or DEFAULT_STRONG,
        openrouter_key=openrouter_key,
        anthropic_key=anthropic_key,
        max_cost_usd=float(_env("LLM_MAX_COST_USD", "5")),
        concurrency=int(_env("LLM_CONCURRENCY", "12")),
    )


def load_settings(data_dir: str | None = None) -> Settings:
    load_dotenv()
    secret = _env("PSEUDONYM_SECRET")
    if not secret:
        raise RuntimeError("PSEUDONYM_SECRET is required (set it in .env or the environment)")
    return Settings(
        data_dir=Path(data_dir or _env("DATA_DIR") or "data"),
        pseudonym_secret=secret,
        fuentes_url=_env("FUENTES_URL"),
        fuentes_token=_env("FUENTES_TOKEN"),
        entrypoint_token=_env("ENTRYPOINT_TOKEN"),
        port=int(_env("PORT", "9000")),
        llm=load_llm_settings(),
    )
