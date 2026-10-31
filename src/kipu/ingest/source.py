"""Source clients: the live sources API (HttpSource) and an on-disk replay of it (DirSource).

Both expose the same resource methods. HttpSource writes every response under
`data/raw/<run_id>/` using the DirSource layout, so any run can be replayed offline.
Nothing is enumerated from a fixed list: channels, dates, docs and tables come from the
listings, so new ones are picked up as the sources grow.
"""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Protocol

import httpx

log = logging.getLogger(__name__)

RETRY_STATUSES = (429, 500, 502, 503, 504)
TIMEOUT = httpx.Timeout(60.0, connect=10.0)


class SourceError(RuntimeError):
    """A resource could not be fetched after all retries."""


class SourceNotFound(SourceError):
    """A listed resource does not exist (404, or a missing file in a replay)."""


class SourceClient(Protocol):
    def channels(self) -> list[dict[str, Any]]: ...
    def users(self) -> list[dict[str, Any]]: ...
    def channel_dates(self, channel: str) -> list[str]: ...
    def messages(self, channel: str, date: str) -> list[dict[str, Any]]: ...
    def docs_index(self) -> list[dict[str, Any]]: ...
    def doc(self, doc_id: str, filename: str) -> bytes: ...
    def tables_index(self) -> list[dict[str, Any]]: ...
    def table(self, name: str) -> bytes: ...
    def record_index(self) -> dict[str, list[str]] | None: ...


class DirSource:
    """Reads a directory laid out like `data/sample/input/`."""

    def __init__(self, path: str | Path) -> None:
        self.root = Path(path)

    def _bytes(self, rel: str) -> bytes:
        try:
            return (self.root / rel).read_bytes()
        except FileNotFoundError as exc:
            raise SourceNotFound(f"{rel} not found in {self.root}") from exc

    def _json(self, rel: str) -> Any:
        return json.loads(self._bytes(rel))

    def channels(self) -> list[dict[str, Any]]:
        return self._json("chats/channels.json")

    def users(self) -> list[dict[str, Any]]:
        return self._json("chats/users.json")

    def channel_dates(self, channel: str) -> list[str]:
        folder = self.root / "chats" / channel
        if not folder.is_dir():
            return []
        return sorted(p.stem for p in folder.glob("*.json") if not p.stem.startswith("_"))

    def messages(self, channel: str, date: str) -> list[dict[str, Any]]:
        return self._json(f"chats/{channel}/{date}.json")

    def docs_index(self) -> list[dict[str, Any]]:
        return self._json("docs/index.json")

    def doc(self, doc_id: str, filename: str) -> bytes:
        return self._bytes(f"docs/{filename}")

    def tables_index(self) -> list[dict[str, Any]]:
        """The saved API listing when present (it carries charset and separator), else every
        file in `tables/`."""
        folder = self.root / "tables"
        if (folder / "_index.json").is_file():
            return self._json("tables/_index.json")
        return [
            {"name": p.name}
            for p in sorted(folder.glob("*"))
            if p.suffix in (".csv", ".xml") and not p.name.startswith("_")
        ]

    def table(self, name: str) -> bytes:
        return self._bytes(f"tables/{name}")

    def record_index(self) -> dict[str, list[str]] | None:
        for candidate in (self.root / "record_index.json", self.root.parent / "record_index.json"):
            if candidate.is_file():
                return json.loads(candidate.read_text(encoding="utf-8"))
        return None


class RateLimiter:
    """Spaces requests evenly so a run stays under the API's 600 requests/minute."""

    def __init__(self, per_minute: int) -> None:
        self.interval = 60.0 / per_minute
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            time.sleep(delay)


class HttpSource:
    """Client for the sources API with ETag revalidation, retries and rate limiting."""

    def __init__(
        self,
        base_url: str,
        token: str,
        raw_dir: str | Path,
        etag_cache: str | Path | None = None,
        per_minute: int = 500,
        max_retries: int = 5,
        backoff: float = 0.5,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.raw = Path(raw_dir)
        self.etag_path = Path(etag_cache) if etag_cache else None
        self.etags: dict[str, dict[str, str]] = self._load_etags()
        self.limiter = RateLimiter(per_minute)
        self.max_retries = max_retries
        self.backoff = backoff
        self.client = httpx.Client(
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT,
            follow_redirects=True,
            transport=transport,
        )
        self.requests = 0

    def _load_etags(self) -> dict[str, dict[str, str]]:
        if self.etag_path and self.etag_path.is_file():
            return json.loads(self.etag_path.read_text(encoding="utf-8"))
        return {}

    def save_etags(self) -> None:
        if self.etag_path:
            self.etag_path.parent.mkdir(parents=True, exist_ok=True)
            self.etag_path.write_text(json.dumps(self.etags, indent=1), encoding="utf-8")

    def _get(self, path: str, save_as: str) -> bytes:
        url = f"{self.base_url}/{path}"
        cached = self.etags.get(url)
        headers = {"If-None-Match": cached["etag"]} if cached else {}
        response = self._request(url, headers)
        if response.status_code == 304 and not (cached and Path(cached["file"]).is_file()):
            response = self._request(url, {})
        if response.status_code == 304:
            data = Path(cached["file"]).read_bytes()
        elif response.status_code == 404:
            raise SourceNotFound(f"GET {url}: 404")
        elif response.is_error:
            raise SourceError(f"GET {url}: HTTP {response.status_code}")
        else:
            data = response.content
        target = self.raw / save_as
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        etag = response.headers.get("etag")
        if etag:
            self.etags[url] = {"etag": etag, "file": str(target)}
        return data

    def _request(self, url: str, headers: dict[str, str]) -> httpx.Response:
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            self.requests += 1
            try:
                response = self.client.get(url, headers=headers)
            except httpx.TransportError as exc:
                if attempt == self.max_retries:
                    raise SourceError(
                        f"GET {url}: {type(exc).__name__} after {attempt + 1} attempts ({exc})"
                    ) from exc
                log.warning("transport error on %s (%s), retrying", url, type(exc).__name__)
            else:
                if response.status_code not in RETRY_STATUSES or attempt == self.max_retries:
                    return response
                log.warning("HTTP %s on %s, retrying", response.status_code, url)
                retry_after = response.headers.get("retry-after", "")
                if retry_after.isdigit():
                    time.sleep(min(60, int(retry_after)))
                    continue
            time.sleep(min(30.0, self.backoff * 2**attempt))
        raise RuntimeError("unreachable")

    def _json(self, path: str, save_as: str) -> Any:
        return json.loads(self._get(path, save_as))

    def channels(self) -> list[dict[str, Any]]:
        return self._json("chats/channels", "chats/channels.json")

    def users(self) -> list[dict[str, Any]]:
        return self._json("chats/users", "chats/users.json")

    def channel_dates(self, channel: str) -> list[str]:
        return self._json(f"chats/{channel}", f"chats/{channel}/_dates.json")

    def messages(self, channel: str, date: str) -> list[dict[str, Any]]:
        return self._json(f"chats/{channel}/{date}", f"chats/{channel}/{date}.json")

    def docs_index(self) -> list[dict[str, Any]]:
        return self._json("docs", "docs/index.json")

    def doc(self, doc_id: str, filename: str) -> bytes:
        return self._get(f"docs/{doc_id}", f"docs/{filename}")

    def tables_index(self) -> list[dict[str, Any]]:
        return self._json("tables", "tables/_index.json")

    def table(self, name: str) -> bytes:
        return self._get(f"tables/{name}", f"tables/{name}")

    def record_index(self) -> dict[str, list[str]] | None:
        return self._json("record_index", "record_index.json")

    def close(self) -> None:
        self.save_etags()
        self.client.close()
