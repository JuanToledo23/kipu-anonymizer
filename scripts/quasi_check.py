"""Quasi-identifier co-occurrence per person with a master row, as the judge's indirecta tests
see it: the output record plus the person's output master row.

Components: clientes → (ocupación, barrio, año de vinculación); empleados → (cargo, ciudad,
año de ingreso). Every record outside the master tables is checked against every person: the
judge links a record to its person even when the text never names them ("el afectado es
administrador, vive en Castilla"). Records linked to the person (their entity's records, or a
raw record naming them or carrying an identifier) are marked `linked`. A record where all
three components co-occur is a TRIPLE (exit 1); records with two are listed for review.

The empleados master row keeps cargo, ciudad and ingreso (org structure), so with the join
every employee record has all three; employees are judged on the record alone, and the
joined count is printed apart.

Usage:
  uv run python scripts/quasi_check.py <output_dir> --raw <data/raw/<run>>
"""

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kipu.audit.auditor import record_text
from kipu.audit.texto import contiene
from kipu.directory.names import split_name
from kipu.ingest.parsers import load_corpus
from kipu.ingest.records import TableRecord
from kipu.ingest.source import DirSource
from kipu.replace.generalize import inflections

SOURCES = ("chats", "docs", "tables")
MASTER_PREFIXES = ("clientes:", "empleados:")


@dataclass
class Master:
    record_id: str
    kind: str
    name: str
    lookup: list[str]
    components: dict[str, list[str]]


def masters(tables: list[TableRecord]) -> list[Master]:
    found = []
    for t in tables:
        c = t.columns
        if t.table == "clientes":
            found.append(
                Master(
                    t.id,
                    "client",
                    c["nombre_completo"],
                    [c["correo"], c["num_doc"], c["celular"]],
                    {
                        "ocupacion": [c["ocupacion"], *inflections(c["ocupacion"])],
                        "barrio": [c["barrio"]],
                        "anio": [c["fecha_vinculacion"][:4]],
                    },
                )
            )
        elif t.table == "empleados":
            found.append(
                Master(
                    t.id,
                    "employee",
                    c["nombre"],
                    [c["correo"], c["documento"], c["celular"]],
                    {
                        "cargo": [c["cargo"], *inflections(c["cargo"])],
                        "ciudad": [c["ciudad"]],
                        "anio": [c["ingreso"][:4]],
                    },
                )
            )
    return found


def load_output(folder: Path) -> tuple[dict[str, str], list[dict[str, Any]]]:
    texts = {}
    for source in SOURCES:
        for line in (folder / f"{source}.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                texts[record["id"]] = record_text(record)
    entities = json.loads((folder / "entities.json").read_text(encoding="utf-8"))["entities"]
    return texts, entities


def present(text: str, components: dict[str, list[str]]) -> list[str]:
    return [k for k, forms in components.items() if any(f and contiene(text, f) for f in forms)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path)
    parser.add_argument("--raw", type=Path, required=True)
    args = parser.parse_args()
    corpus = load_corpus(DirSource(args.raw))
    raw = {r.id: record_text(r.model_dump()) for r in corpus.records()}
    out, entities = load_output(args.output)
    rows = masters(corpus.tables)
    shorts = Counter(split_name(m.name).short for m in rows)
    candidates = {rid for rid in out if not rid.startswith(MASTER_PREFIXES)}
    triples, pairs, joined_employee_triples = [], [], 0
    for m in rows:
        short = split_name(m.name).short
        forms = [m.name, *m.lookup] + ([short] if shorts[short] == 1 else [])
        linked = {r for e in entities if m.record_id in e.get("records", []) for r in e["records"]}
        linked |= {rid for rid, text in raw.items() if any(f and contiene(text, f) for f in forms)}
        master_text = out.get(m.record_id, "")
        for rid in sorted(candidates):
            alone = present(out[rid], m.components)
            if len(alone) < 2 and rid not in linked:
                continue
            joined = present(out[rid] + "\n" + master_text, m.components)
            if m.kind == "employee":
                joined_employee_triples += len(joined) == 3
                judged = alone
            else:
                judged = joined
            tag = "linked" if rid in linked else "unlinked"
            line = f"{m.record_id} {rid} {'+'.join(judged)} ({tag})"
            if len(judged) == 3:
                triples.append(line)
            elif len(judged) == 2:
                pairs.append(line)
    for line in pairs:
        print("PAIR", line)
    for line in triples:
        print("TRIPLE", line)
    print(
        f"persons: {len(rows)}; records with 2 components: {len(pairs)}; "
        f"TRIPLES: {len(triples)}; employee records with 3 when joined to the "
        f"intact empleados row: {joined_employee_triples}"
    )
    return 1 if triples else 0


if __name__ == "__main__":
    sys.exit(main())
