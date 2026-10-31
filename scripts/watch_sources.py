"""Watch the sources API for growth. A standalone helper, not part of the service.

Usage:
  uv run python scripts/watch_sources.py [--interval 120] [--once]

Polls `GET {FUENTES_URL}/record_index` (FUENTES_URL and FUENTES_TOKEN from the environment or
.env; the token is never printed) with If-None-Match, so an unchanged index costs one 304.
Prints the id count per source on every poll. When a count changes it prints a loud line and
writes `data/sources_changed.flag` (old and new counts). The last ETag and counts persist in
`data/sources_watch.json`, so a restarted watcher compares against what it saw before; with no
state yet, `data/fuentes/record_index.json` (the index saved at the start) is the baseline.
"""

import argparse
import datetime as dt
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

SOURCES = ("chats", "docs", "tables")
STATE = Path("data/sources_watch.json")
FLAG = Path("data/sources_changed.flag")
BASELINE = Path("data/fuentes/record_index.json")


def counts_of(index: dict[str, list[str]]) -> dict[str, int]:
    return {source: len(index.get(source, [])) for source in SOURCES}


def load_state(state: Path, baseline: Path) -> dict[str, Any]:
    if state.is_file():
        return json.loads(state.read_text(encoding="utf-8"))
    if baseline.is_file():
        return {"etag": None, "counts": counts_of(json.loads(baseline.read_text("utf-8")))}
    return {"etag": None, "counts": None}


def poll(
    client: httpx.Client, url: str, state: dict[str, Any], state_path: Path, flag: Path
) -> bool:
    """One poll; True when the counts changed since the last one seen."""
    now = dt.datetime.now().isoformat(timespec="seconds")
    headers = {"If-None-Match": state["etag"]} if state.get("etag") else {}
    response = client.get(f"{url.rstrip('/')}/record_index", headers=headers)
    if response.status_code == 304:
        print(f"{now} unchanged (304) {_fmt(state['counts'])}", flush=True)
        return False
    response.raise_for_status()
    counts = counts_of(response.json())
    previous = state.get("counts")
    changed = previous is not None and counts != previous
    if changed:
        print(f"{now} !!!!! SOURCES CHANGED !!!!! {_fmt(previous)} -> {_fmt(counts)}", flush=True)
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text(json.dumps({"at": now, "before": previous, "after": counts}) + "\n")
    else:
        print(f"{now} {_fmt(counts)}", flush=True)
    state.update(etag=response.headers.get("etag"), counts=counts)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=1), encoding="utf-8")
    return changed


def _fmt(counts: dict[str, int] | None) -> str:
    if not counts:
        return "(no baseline)"
    return " ".join(f"{s}={counts[s]}" for s in SOURCES) + f" total={sum(counts.values())}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll record_index and report source growth")
    parser.add_argument("--interval", type=float, default=120.0, help="seconds between polls")
    parser.add_argument("--once", action="store_true", help="poll once and exit")
    args = parser.parse_args()
    load_dotenv()
    url, token = os.environ.get("FUENTES_URL", "").strip(), os.environ.get("FUENTES_TOKEN", "")
    if not url or not token.strip():
        raise SystemExit("FUENTES_URL and FUENTES_TOKEN are required")
    state = load_state(STATE, BASELINE)
    headers = {"Authorization": f"Bearer {token.strip()}"}
    with httpx.Client(headers=headers, timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        while True:
            try:
                poll(client, url, state, STATE, FLAG)
            except httpx.HTTPError as exc:
                print(f"poll failed: {type(exc).__name__}: {exc}", flush=True)
            if args.once:
                return
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
