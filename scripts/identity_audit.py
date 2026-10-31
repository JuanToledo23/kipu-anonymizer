"""Identity audit of a run: the directory rebuilt from the raw corpus over a copy of the
registry (the registry itself is never touched), checked against the output entities.

  a  persons sharing a normalized full name, with the identifiers that tell them apart
  b  users.json clients without email left as separate persons, with the clientes rows
     whose name matches
  c  employees of users.json without their empleados row (name forms differ)
  d  persons whose identifiers or full name occur in a raw record their entity misses
  e  an original form (identifier or name) declared by two entities
  f  with --baseline: entities whose pseudonyms differ from an earlier entities.json

Usage:
  uv run python scripts/identity_audit.py <output_dir> --raw <data/raw/<run>> \
      --registry <registry.sqlite> [--baseline <earlier output>/entities.json]
"""

import argparse
import json
import shutil
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from kipu.audit.auditor import record_text
from kipu.audit.texto import contiene, norm
from kipu.directory.directory import Directory, Person
from kipu.directory.names import fold, split_name
from kipu.directory.registry import Registry
from kipu.ingest.parsers import load_corpus
from kipu.ingest.source import DirSource
from kipu.pipeline import build_directory

DISTINCT = ("cedula", "cliente_id", "kp_id", "uid")
LOOKUP = ("email", "cedula", "phone")
MIN_FORM = 6


def evidence(person: Person) -> str:
    ids = {t: sorted(person.identifiers.get(t, set())) for t in DISTINCT}
    return f"{person.person_id}[{person.kind}] " + " ".join(
        f"{t}={','.join(v)}" for t, v in ids.items() if v
    )


def same_name(directory: Directory) -> list[str]:
    by_name: dict[str, list[Person]] = defaultdict(list)
    for person in directory.persons.values():
        if person.kind != "external" and person.name.full:
            by_name[fold(person.name.full)].append(person)
    lines = []
    for name, persons in sorted(by_name.items()):
        if len(persons) > 1:
            lines.append(f"a {name}: " + " | ".join(evidence(p) for p in persons))
    return lines


def unlinked_users(directory: Directory, corpus_users: list[dict], rows: list) -> list[str]:
    lines = []
    for user in corpus_users:
        profile = user.get("profile", {})
        title = profile.get("title")
        if user.get("is_bot") or title is None or title == "Asistente virtual":
            continue
        owner = directory.owner_of("uid", user["id"])
        person = directory.persons.get(owner or "")
        name = user.get("real_name") or profile.get("real_name") or ""
        if title == "Cliente":
            if person is not None and person.identifiers.get("cliente_id"):
                continue
            keys = {fold(split_name(name).full), fold(split_name(name).short)}
            matches = [
                r.id
                for r in rows
                if r.table == "clientes"
                and keys
                & {
                    fold(r.columns["nombre_completo"]),
                    fold(split_name(r.columns["nombre_completo"]).short),
                }
            ]
            email = "with" if profile.get("email") else "without"
            lines.append(f"b {user['id']} '{name}' ({email} email) → {owner}; candidates {matches}")
        elif person is None or not person.identifiers.get("kp_id"):
            matches = [
                r.id
                for r in rows
                if r.table == "empleados"
                and (
                    fold(r.columns.get("correo", "")) == fold(profile.get("email", ""))
                    or fold(split_name(r.columns["nombre"]).short) == fold(split_name(name).short)
                )
            ]
            lines.append(f"c {user['id']} '{name}' ({title}) → {owner}; candidates {matches}")
    return lines


def missed_records(
    directory: Directory, entities: list[dict], raw: dict[str, str], names_unique: set[str]
) -> list[str]:
    by_id = {e["entity_id"]: e for e in entities}
    lines = []
    for person in directory.persons.values():
        entity = by_id.get(person.person_id)
        if entity is None or person.kind == "external":
            continue
        forms = [v for t in LOOKUP for v in person.identifiers.get(t, set()) if len(v) >= MIN_FORM]
        if fold(person.name.full) in names_unique:
            forms.append(person.name.full)
        records = set(entity["records"])
        missing = sorted(
            rid
            for rid, text in raw.items()
            if rid not in records and any(contiene(text, f) for f in forms)
        )
        if missing:
            sources = sorted({_source(r) for r in records})
            lines.append(f"d {person.person_id} in {sources}; also named in {missing[:6]}")
    return lines


def shared_forms(entities: list[dict]) -> list[str]:
    owners: dict[str, set[str]] = defaultdict(set)
    for e in entities:
        if e.get("kept"):
            continue
        for form in e.get("originals", []):
            owners[norm(form)].add(e["entity_id"])
    return [
        f"e '{form}' declared by {sorted(ids)}"
        for form, ids in sorted(owners.items())
        if len(ids) > 1
    ]


def changed_pseudonyms(entities: list[dict], baseline: list[dict]) -> list[str]:
    before = {e["entity_id"]: e.get("pseudonyms", {}) for e in baseline if not e.get("kept")}
    lines = []
    for e in entities:
        old = before.get(e["entity_id"])
        if e.get("kept") or old is None:
            continue
        diff = {k: (old.get(k), v) for k, v in e["pseudonyms"].items() if old.get(k) != v}
        diff |= {k: (v, None) for k, v in old.items() if k not in e["pseudonyms"]}
        if diff:
            lines.append(f"f {e['entity_id']} {diff}")
    missing = sorted(set(before) - {e["entity_id"] for e in entities})
    if missing:
        lines.append(f"f entities gone since the baseline: {missing}")
    return lines


def _source(record_id: str) -> str:
    if ":" in record_id:
        return "tables"
    return "docs" if record_id.startswith(("doc_", "sdoc_")) else "chats"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    corpus = load_corpus(DirSource(args.raw))
    raw = {r.id: record_text(r.model_dump()) for r in corpus.records()}
    entities = json.loads((args.output / "entities.json").read_text(encoding="utf-8"))["entities"]
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "registry.sqlite"
        shutil.copy(args.registry, copy)
        registry = Registry(copy)
        directory, _ = build_directory(corpus, registry)
        names = defaultdict(int)
        for person in directory.persons.values():
            names[fold(person.name.full)] += 1
        unique = {n for n, count in names.items() if count == 1}
        findings = (
            same_name(directory)
            + unlinked_users(directory, corpus.users, corpus.tables)
            + missed_records(directory, entities, raw, unique)
            + shared_forms(entities)
        )
        if args.baseline:
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))["entities"]
            findings += changed_pseudonyms(entities, baseline)
        registry.close()
    for line in findings:
        print(line)
    counts = defaultdict(int)
    for line in findings:
        counts[line[0]] += 1
    print("findings:", dict(sorted(counts.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
