"""A fake sources API (same paths as the real one) serving any DirSource directory.

`FakeSources` is an httpx transport handler: pass `httpx.MockTransport(fake)` to the app or to
HttpSource. Faults (status codes served before the real answer), a per-request delay and a gate
that holds one path until released let tests reproduce flaky or slow sources.
"""

import json
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

from kipu.ingest.source import DirSource, SourceNotFound


class FakeSources:
    def __init__(self, root: str | Path, token: str = "tok", prefix: str = "/v1") -> None:
        self.source = DirSource(root)
        self.token = token
        self.prefix = prefix
        self.faults: dict[str, list[int]] = defaultdict(list)
        self.delay = 0.0
        self.gate_path: str | None = None
        self.gate = threading.Event()
        self.reached_gate = threading.Event()
        self.requests: list[httpx.Request] = []
        self.lock = threading.Lock()

    def hold(self, path: str) -> None:
        """Block requests to `path` until `release()`."""
        self.gate_path = path
        self.gate.clear()

    def release(self) -> None:
        self.gate.set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            self.requests.append(request)
        path = request.url.path
        if self.delay:
            time.sleep(self.delay)
        if path == self.gate_path:
            self.reached_gate.set()
            self.gate.wait(timeout=30)
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"error": "bad token"})
        with self.lock:
            fault = self.faults[path].pop(0) if self.faults[path] else None
        if fault:
            return httpx.Response(fault, json={"error": "injected"})
        try:
            body = self._serve(path.removeprefix(self.prefix).strip("/").split("/"))
        except (SourceNotFound, KeyError, IndexError):
            return httpx.Response(404, json={"error": "not found"})
        if isinstance(body, bytes):
            return httpx.Response(200, content=body)
        return httpx.Response(200, content=json.dumps(body, ensure_ascii=False).encode("utf-8"))

    def _serve(self, parts: list[str]) -> Any:
        s = self.source
        match parts:
            case ["chats", "channels"]:
                return s.channels()
            case ["chats", "users"]:
                return s.users()
            case ["chats", channel]:
                return self._dates(channel)
            case ["chats", channel, date]:
                return s.messages(channel, date)
            case ["docs"]:
                return s.docs_index()
            case ["docs", doc_id]:
                entry = next(d for d in s.docs_index() if d["id"] == doc_id)
                return s.doc(doc_id, entry["filename"])
            case ["tables"]:
                return s.tables_index()
            case ["tables", name]:
                return s.table(name)
            case ["record_index"]:
                index = s.record_index()
                if index is None:
                    raise SourceNotFound("record_index")
                return index
        raise SourceNotFound("/".join(parts))

    def _dates(self, channel: str) -> list[str]:
        """The saved listing when present (it may list a date with no file), else the files."""
        listing = self.source.root / "chats" / channel / "_dates.json"
        if listing.is_file():
            return json.loads(listing.read_text(encoding="utf-8"))
        dates = self.source.channel_dates(channel)
        if not dates:
            raise SourceNotFound(channel)
        return dates

    def hits(self, path: str) -> int:
        return sum(1 for r in self.requests if r.url.path == path)
