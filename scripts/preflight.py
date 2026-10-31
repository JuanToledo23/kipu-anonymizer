"""Hostile pre-flight sweep of an output dir against the raw corpus it was built from.

Usage:
  uv run python scripts/preflight.py <output_dir> --raw <data/raw/<run>> [--public <dir>]

Checks (exit 1 on any real hit):
  3a  master-table values (clientes, empleados; cedula and agente of pqrs)
  3b  users.json real_name, handle, email and phone
  3c  doc metadata values that name a person or carry an email
  3d  emails, phones, cédulas and plates in the output that are not declared surrogates
  3e  UPPERCASE-without-accents forms of every directory name
  4   structure: ids vs record_index.json, table columns, doc metadata keys, attachments

Categorical columns (tipo_doc, barrio, ocupacion, notas) are not identifiers and are skipped:
they repeat across persons by design (barrio and ocupacion are generalized per person).
"""

import argparse
import json
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kipu.audit.auditor import flatten
from kipu.audit.texto import contiene
from kipu.detect.patterns import CEDULA_CONTEXT, PLATE
from kipu.directory.names import GIVEN, fold, split_name, strip_accents
from kipu.ingest.parsers import RawCorpus, load_corpus
from kipu.ingest.source import DirSource

SOURCES = ("chats", "docs", "tables")
SKIPPED_COLUMNS = {
    "clientes": {
        "cliente_id",
        "ciudad",
        "fecha_vinculacion",
        "tipo_doc",
        "barrio",
        "ocupacion",
        "notas",
    },
    "empleados": {"id", "reporta_a", "ciudad", "ingreso", "cargo", "documento_tipo"},
}
PQRS_COLUMNS = ("cedula", "agente")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?<![\d.,])(?:\+?57[ -]?)?(3\d{2}[ -]?\d{3}[ -]?\d{4})(?![\d.,])")
# A trailing `-d` is a NIT check digit (companies), never part of a cédula.
NUMBER = re.compile(r"(?<![\d.,$\w-])(\d{1,3}(?:\.\d{3}){1,3}|\d{6,10})(?![\d,\w]|\.\d|-\d(?!\d))")
MAX_LOCATIONS = 5


@dataclass
class Hit:
    check: str
    source: str
    form: str
    record_id: str
    field: str


@dataclass
class Output:
    records: dict[str, list[tuple[str, dict[str, Any]]]]
    entities: list[dict[str, Any]]
    fields: list[tuple[str, str, str]] = field(default_factory=list)
    whole: str = ""

    @classmethod
    def load(cls, folder: Path) -> "Output":
        records: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for source in SOURCES:
            for line in (folder / f"{source}.jsonl").read_text(encoding="utf-8").splitlines():
                if line.strip():
                    record = json.loads(line)
                    records.setdefault(record["id"], []).append((source, record))
        entities = json.loads((folder / "entities.json").read_text(encoding="utf-8"))["entities"]
        out = cls(records, entities)
        out.fields = [
            (rid, name, text)
            for rid, versions in records.items()
            for _, record in versions
            for name, text in _fields(record)
        ]
        out.whole = "\n".join(text for _, _, text in out.fields)
        return out

    def locate(self, needle: str) -> list[tuple[str, str]]:
        if not contiene(self.whole, needle):
            return []
        return [(rid, f) for rid, f, text in self.fields if contiene(text, needle)]


def _fields(record: dict[str, Any]) -> Iterator[tuple[str, str]]:
    for key, value in record.items():
        if key == "id":
            continue
        if isinstance(value, dict):
            for sub, inner in value.items():
                yield f"{key}.{sub}", " | ".join(flatten(inner))
        else:
            yield key, " | ".join(flatten(value))


class Sweep:
    def __init__(self, out: Output, corpus: RawCorpus) -> None:
        self.out = out
        self.corpus = corpus
        self.hits: list[Hit] = []
        self.checked: dict[str, int] = {}
        self.info: list[str] = []

    def _value(self, check: str, source: str, value: str) -> None:
        self.checked[check] = self.checked.get(check, 0) + 1
        for rid, f in self.out.locate(value)[:MAX_LOCATIONS]:
            self.hits.append(Hit(check, source, value, rid, f))

    def master_tables(self) -> None:
        self.checked["3a"] = 0
        for t in self.corpus.tables:
            if t.table in SKIPPED_COLUMNS:
                items = [(c, v) for c, v in t.columns.items() if c not in SKIPPED_COLUMNS[t.table]]
            elif t.table == "pqrs":
                items = [(c, t.columns.get(c, "")) for c in PQRS_COLUMNS]
            else:
                continue
            for column, value in items:
                if value.strip():
                    self._value("3a", f"{t.id}.{column}", value)

    def users(self) -> None:
        self.checked["3b"] = 0
        for user in self.corpus.users:
            if user.get("is_bot") or user.get("deleted"):
                continue
            profile = user.get("profile", {})
            for label, value in (
                ("real_name", user.get("real_name")),
                ("name", user.get("name")),
                ("profile.email", profile.get("email")),
                ("profile.phone", profile.get("phone")),
            ):
                if value:
                    self._value("3b", f"{user['id']}.{label}", value)

    def doc_metadata(self) -> None:
        self.checked["3c"] = 0
        values = [
            (d.id, f"metadata.{k}", v) for d in self.corpus.docs for k, v in d.metadata.items()
        ]
        for doc_id, xmp in self.corpus.pdf_xmp.items():
            values += [
                (doc_id, "xmp", m.group(1).strip()) for m in re.finditer(r">([^<>]{3,})<", xmp)
            ]
        candidates: set[tuple[str, str, str]] = set()
        for doc_id, key, value in values:
            candidates.update((doc_id, key, e) for e in EMAIL.findall(value))
            for part in re.split(r"[;,]| - ", value):
                if _names_a_person(part.strip()):
                    candidates.add((doc_id, key, part.strip()))
        for doc_id, key, value in sorted(candidates):
            self._value("3c", f"{doc_id}.{key}", value)

    def shapes(self) -> None:
        """Identifier-shaped strings that are not declared surrogates. Numbers count as hits only
        when they are a real directory value or follow a cédula context word."""
        declared = self._declared()
        real_numbers = self._real_numbers()
        counts = {"email": 0, "phone": 0, "number": 0, "plate": 0}
        unverified = 0
        for rid, f, text in self.out.fields:
            for m in EMAIL.finditer(text):
                counts["email"] += 1
                if m.group(0).lower() not in declared:
                    self.hits.append(Hit("3d", "email", m.group(0), rid, f))
            for m in PHONE.finditer(text):
                counts["phone"] += 1
                if _digits(m.group(1)) not in declared:
                    self.hits.append(Hit("3d", "phone", m.group(0), rid, f))
            for m in PLATE.finditer(text):
                counts["plate"] += 1
                if re.sub(r"[- ]", "", m.group(1)).lower() not in declared:
                    self.hits.append(Hit("3d", "plate", m.group(1), rid, f))
            for m in NUMBER.finditer(text):
                digits = _digits(m.group(1))
                if not 6 <= len(digits) <= 10 or digits in declared or PHONE.fullmatch(m.group(1)):
                    continue
                counts["number"] += 1
                before = text[max(0, m.start() - 30) : m.start()]
                if digits in real_numbers or CEDULA_CONTEXT.search(before):
                    self.hits.append(Hit("3d", "cedula", m.group(1), rid, f))
                else:
                    unverified += 1
        self.checked["3d"] = sum(counts.values())
        self.info.append(
            f"3d shapes seen: {counts}; {unverified} numbers with no cédula context and no real "
            "directory value (amounts, NITs) are not counted"
        )

    def _declared(self) -> set[str]:
        values: set[str] = set()
        for entity in self.out.entities:
            for value in (entity.get("pseudonyms") or {}).values():
                if isinstance(value, str):
                    values.update(
                        {value.lower(), _digits(value), re.sub(r"[- ]", "", value).lower()}
                    )
        values.discard("")
        return values

    def _real_numbers(self) -> set[str]:
        numbers: set[str] = set()
        for t in self.corpus.tables:
            for column in ("num_doc", "documento", "cedula", "celular"):
                if t.columns.get(column):
                    numbers.add(_digits(t.columns[column]))
        for user in self.corpus.users:
            if user.get("profile", {}).get("phone"):
                numbers.add(_digits(user["profile"]["phone"]))
        numbers.discard("")
        return numbers

    def uppercase(self) -> None:
        names: dict[str, str] = {}
        for t in self.corpus.tables:
            name = t.columns.get("nombre_completo") or (
                t.columns.get("nombre") if t.table == "empleados" else ""
            )
            if name:
                names[name] = t.id
        for user in self.corpus.users:
            if not user.get("is_bot") and user.get("real_name"):
                names[user["real_name"]] = user["id"]
        folded = [(rid, f, strip_accents(text)) for rid, f, text in self.out.fields]
        whole = "\n".join(text for _, _, text in folded)
        self.checked["3e"] = 0
        for name, source in names.items():
            parts = split_name(name)
            for form in {strip_accents(x).upper() for x in (name, parts.first, parts.short)}:
                if len(form) < 3:
                    continue
                self.checked["3e"] += 1
                pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(form)}(?![A-Za-z0-9])")
                if not pattern.search(whole):
                    continue
                located = [(rid, f) for rid, f, text in folded if pattern.search(text)]
                for rid, f in located[:MAX_LOCATIONS]:
                    self.hits.append(Hit("3e", source, form, rid, f))

    def structure(self, index: dict[str, list[str]]) -> None:
        issues: list[str] = []
        records = self.out.records
        for source, ids in index.items():
            for rid in ids:
                count = sum(1 for s, _ in records.get(rid, []) if s == source)
                if count != 1:
                    issues.append(f"{source}:{rid} present {count}x")
        expected = {rid for ids in index.values() for rid in ids}
        issues += [f"{rid} not in record_index" for rid in sorted(set(records) - expected)]
        raw = {r.id: r for r in self.corpus.records()}
        for rid, versions in records.items():
            for source, record in versions:
                issues += _structure_issues(source, record, raw.get(rid))
        self.checked["4"] = len(records)
        self.hits += [Hit("4", "structure", issue, "", "") for issue in issues]


def _structure_issues(source: str, record: dict[str, Any], raw: Any) -> list[str]:
    rid = record["id"]
    if source == "tables":
        columns = record["columns"]
        if not all(isinstance(v, str) for v in columns.values()):
            return [f"{rid} has a non-string column"]
        if raw is not None and list(columns) != list(raw.columns):
            return [f"{rid} columns differ from raw"]
    elif source == "docs":
        if raw is not None and set(record["metadata"]) != set(raw.metadata):
            return [f"{rid} metadata keys differ from raw"]
    elif source == "chats" and raw is not None:
        if len(record["attachments"]) != len(raw.attachments):
            return [
                f"{rid} attachment count {len(raw.attachments)} -> {len(record['attachments'])}"
            ]
    return []


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _names_a_person(value: str) -> bool:
    """A given name followed by a capitalized word, or a `first.last` handle."""
    if re.fullmatch(r"[a-z]+\.[a-z]+", value):
        return True
    words = value.split()
    return (
        len(words) >= 2
        and fold(words[0]) in GIVEN
        and words[0][:1].isupper()
        and words[1][:1].isupper()
    )


def print_report(sweep: Sweep) -> None:
    checks = ("3a", "3b", "3c", "3d", "3e", "4")
    print(f"{'check':<6} {'checked':>8} {'hits':>6}  result")
    for check in checks:
        n = sum(1 for h in sweep.hits if h.check == check)
        print(f"{check:<6} {sweep.checked.get(check, 0):>8} {n:>6}  {'FAIL' if n else 'PASS'}")
    for line in sweep.info:
        print(line)
    if sweep.hits:
        print("\nhits:")
        for h in sweep.hits:
            print(f"  {h.check:<3} {h.source:<32} {h.form!r:<34} {h.record_id} {h.field}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Hostile sweep of an output dir")
    parser.add_argument("output", type=Path)
    parser.add_argument("--raw", type=Path, required=True, help="raw run dir (data/raw/<run>)")
    parser.add_argument("--public", type=Path, help="dir with record_index.json (default: --raw)")
    args = parser.parse_args()
    corpus = load_corpus(DirSource(args.raw))
    index_dir = args.public or args.raw
    index = json.loads((index_dir / "record_index.json").read_text(encoding="utf-8"))
    sweep = Sweep(Output.load(args.output), corpus)
    sweep.master_tables()
    sweep.users()
    sweep.doc_metadata()
    sweep.shapes()
    sweep.uppercase()
    sweep.structure(index)
    print_report(sweep)
    return 1 if sweep.hits else 0


if __name__ == "__main__":
    sys.exit(main())
