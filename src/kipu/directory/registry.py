"""Persistent person registry in SQLite. Rows are only ever added; it is never rebuilt."""

import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS persons (
    person_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    gender TEXT NOT NULL,
    created_at REAL NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    attrs TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS identifiers (
    person_id TEXT NOT NULL REFERENCES persons(person_id),
    type TEXT NOT NULL,
    value_norm TEXT NOT NULL,
    value_raw TEXT NOT NULL,
    source TEXT NOT NULL,
    UNIQUE(type, value_norm)
);
CREATE TABLE IF NOT EXISTS surrogates (
    person_id TEXT NOT NULL REFERENCES persons(person_id),
    field TEXT NOT NULL,
    original_norm TEXT NOT NULL DEFAULT '',
    value TEXT NOT NULL,
    value_norm TEXT NOT NULL,
    UNIQUE(field, value_norm),
    UNIQUE(person_id, field, original_norm)
);
CREATE TABLE IF NOT EXISTS observed_forms (
    person_id TEXT NOT NULL,
    form TEXT NOT NULL,
    record_id TEXT NOT NULL,
    UNIQUE(person_id, form, record_id)
);
CREATE TABLE IF NOT EXISTS journal (ts REAL NOT NULL, op TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
"""


class Registry:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level=None, timeout=30
        )
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.lock = threading.RLock()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            self.conn.execute("BEGIN")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            self.conn.execute("COMMIT")

    def journal(self, op: str, payload: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO journal VALUES (?, ?, ?)",
            (time.time(), op, json.dumps(payload, ensure_ascii=False)),
        )

    def new_person(self, kind: str, gender: str, name: str, attrs: dict[str, str]) -> str:
        with self.transaction() as c:
            row = c.execute("SELECT value FROM counters WHERE name='person'").fetchone()
            value = (row[0] if row else 0) + 1
            c.execute(
                "INSERT INTO counters VALUES ('person', ?) "
                "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                (value,),
            )
            person_id = f"p_{value:03d}"
            c.execute(
                "INSERT INTO persons VALUES (?, ?, ?, ?, ?, ?)",
                (person_id, kind, gender, time.time(), name, json.dumps(attrs, ensure_ascii=False)),
            )
            self.journal("new_person", {"person_id": person_id, "kind": kind})
        return person_id

    def update_profile(self, person_id: str, name: str, attrs: dict[str, str]) -> None:
        with self.lock:
            self.conn.execute(
                "UPDATE persons SET name=?, attrs=? WHERE person_id=?",
                (name, json.dumps(attrs, ensure_ascii=False), person_id),
            )

    def owner(self, id_type: str, value_norm: str) -> str | None:
        row = self.conn.execute(
            "SELECT person_id FROM identifiers WHERE type=? AND value_norm=?", (id_type, value_norm)
        ).fetchone()
        return row[0] if row else None

    def add_identifier(
        self, person_id: str, id_type: str, value_norm: str, value_raw: str, source: str
    ) -> bool:
        """Attach an identifier; False if another person already owns it."""
        with self.lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO identifiers VALUES (?, ?, ?, ?, ?)",
                (person_id, id_type, value_norm, value_raw, source),
            )
            if cur.rowcount:
                return True
            return self.owner(id_type, value_norm) == person_id

    def values_of(self, person_id: str, id_type: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT value_norm FROM identifiers WHERE person_id=? AND type=?", (person_id, id_type)
        ).fetchall()
        return {r[0] for r in rows}

    def persons(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT person_id, kind, gender, name, attrs FROM persons ORDER BY person_id"
        ).fetchall()
        return [
            {
                "person_id": r[0],
                "kind": r[1],
                "gender": r[2],
                "name": r[3],
                "attrs": json.loads(r[4]),
            }
            for r in rows
        ]

    def identifiers(self) -> list[tuple[str, str, str, str]]:
        return self.conn.execute(
            "SELECT person_id, type, value_norm, value_raw FROM identifiers"
        ).fetchall()

    def surrogate(self, person_id: str, field: str, original_norm: str = "") -> str | None:
        row = self.conn.execute(
            "SELECT value FROM surrogates WHERE person_id=? AND field=? AND original_norm=?",
            (person_id, field, original_norm),
        ).fetchone()
        return row[0] if row else None

    def reserve_surrogate(
        self, person_id: str, field: str, original_norm: str, value: str, value_norm: str
    ) -> bool:
        """Reserve a surrogate value; False when another person already holds it."""
        with self.lock:
            try:
                self.conn.execute(
                    "INSERT INTO surrogates VALUES (?, ?, ?, ?, ?)",
                    (person_id, field, original_norm, value, value_norm),
                )
            except sqlite3.IntegrityError:
                return False
            self.journal("reserve", {"person_id": person_id, "field": field})
            return True

    def surrogate_values(self) -> list[tuple[str, str]]:
        return self.conn.execute("SELECT field, value FROM surrogates").fetchall()

    def record_forms(self, rows: list[tuple[str, str, str]]) -> None:
        with self.transaction() as c:
            c.executemany("INSERT OR IGNORE INTO observed_forms VALUES (?, ?, ?)", rows)

    def close(self) -> None:
        self.conn.close()
