"""Settings from the environment (and `.env` when present). Secrets are never logged."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    pseudonym_secret: str
    fuentes_url: str
    fuentes_token: str
    entrypoint_token: str
    port: int

    @property
    def registry_path(self) -> Path:
        return self.data_dir / "registry.sqlite"

    @property
    def output_dir(self) -> Path:
        return self.data_dir / "output"


def _env(name: str, default: str = "") -> str:
    # `docker --env-file` keeps trailing spaces that python-dotenv strips; both must agree.
    return os.environ.get(name, default).strip()


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
    )
