"""Run the live /anonymize red-team cases against a service and print PASS/FAIL per case.

Usage:
  uv run python tests/redteam/run_anonymize_cases.py --raw data/raw/<run> [--url http://localhost:9000/v1]

Original forms are filled in from the raw run at test time and never printed. The bearer token
comes from ENTRYPOINT_TOKEN when set.
"""

import argparse
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from kipu.audit.auditor import flatten
from kipu.audit.texto import contiene
from kipu.directory.names import fold, split_name, strip_accents
from kipu.ingest.parsers import RawCorpus, load_corpus
from kipu.ingest.source import DirSource

CASES = Path(__file__).with_name("anonymize_cases.json")
CYRILLIC = str.maketrans({"a": "а", "e": "е", "o": "о", "c": "с", "p": "р"})
ROW_COLUMNS = (
    "nombre_completo",
    "nombre",
    "num_doc",
    "documento",
    "cedula",
    "correo",
    "celular",
    "direccion",
    "agente",
)


@dataclass
class Case:
    spec: dict[str, Any]
    forms: dict[str, str]
    records: list[dict[str, Any]] = field(default_factory=list)
    absent: list[str] = field(default_factory=list)


def person_forms(corpus: RawCorpus, ref: str | None) -> dict[str, str]:
    if not ref:
        return {}
    kind, key = ref.split(":", 1)
    if kind == "users":
        user = next(u for u in corpus.users if u["id"] == key)
        prof = user.get("profile", {})
        name, email, phone, cedula, handle = (
            user["real_name"],
            prof.get("email", ""),
            prof.get("phone", ""),
            "",
            user["name"],
        )
    else:
        row = next(t.columns for t in corpus.tables if t.id == ref)
        name = row.get("nombre_completo") or row["nombre"]
        email, phone = row["correo"], row["celular"]
        cedula, handle = row.get("num_doc") or row.get("documento", ""), ""
    parts = split_name(name)
    local, _, domain = email.partition("@")
    return {
        "name": name,
        "NAME_UPPER": strip_accents(name).upper(),
        "name_cyr": name.translate(CYRILLIC),
        "name_nl": f"{parts.first}\n{' '.join(parts.surnames)}",
        "name_slug": strip_accents(name).lower().replace(" ", "-"),
        "first": parts.first,
        "first_lower": strip_accents(parts.given[0]).lower(),
        "email": email,
        "email_local": local,
        "email_spoken": f"{local.replace('.', ' punto ')} arroba {domain.replace('.', ' punto ')}",
        "cedula": cedula,
        "phone": phone,
        "phone_spaced": f"+57 {phone[:3]} {phone[3:6]} {phone[6:8]} {phone[8:]}" if phone else "",
        "handle": handle,
    }


def fill(template: str, forms: dict[str, str]) -> str:
    return template.format(**forms) if forms else template


def build(corpus: RawCorpus, spec: dict[str, Any], cases_file: dict[str, Any]) -> Case:
    forms = person_forms(corpus, spec.get("person"))
    case = Case(spec, forms)
    raw = {r.id: r.model_dump() for r in corpus.records()}
    if spec["source"] == "chats":
        texts = spec.get("texts") or [spec["text"]]
        for i, text in enumerate(texts):
            record = dict(raw[cases_file["base_chat"]])
            record.update(id=f"rt-{spec['id']}-{i}", text=fill(text, forms), attachments=[])
            case.records.append(record)
        if "first_lower" in forms:
            forms["lit_email_new"] = f"{forms['first_lower']}.nuevo2026@protonmail.com"
    elif spec["source"] == "docs":
        record = dict(raw[cases_file["base_doc"]])
        record.update(
            id=f"rt-{spec['id']}",
            body=fill(spec["body"], forms),
            metadata={k: fill(v, forms) for k, v in spec["metadata"].items()},
        )
        case.records.append(record)
    else:
        for ref in spec["tables"]:
            table, key = ref.split(":", 1)
            if key == "first":
                record = next(r for r in raw.values() if r.get("table") == table)
            else:
                record = raw[ref]
            case.records.append(record)
            for column in ROW_COLUMNS:
                value = record["columns"].get(column, "")
                if value:
                    case.absent.append(value)
    for key in spec["absent"]:
        if key.startswith("lit:"):
            case.absent.append(key[4:])
        elif key != "row_values" and forms.get(key):
            case.absent.append(forms[key])
    return case


def expected_entity(entities: list[dict[str, Any]], forms: dict[str, str]) -> dict[str, Any] | None:
    keys = {forms.get("name"), forms.get("email"), forms.get("cedula"), forms.get("handle")} - {
        "",
        None,
    }
    for e in entities:
        if e.get("type") == "person" and keys & set(e.get("originals", [])):
            return e
    return None


def named_entities(case: Case, live: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Person entities that carry the name the case writes in the text, never the record's
    author: a stale registry person behind the author must not count as the new person."""
    named = fold(case.forms.get("name") or case.spec["new_person"]["name"])
    authors = {fold(r["author"]) for r in case.records if r.get("author")}
    found = []
    for e in live:
        originals = {fold(o) for o in e.get("originals", [])}
        if e.get("type") == "person" and named in originals and not originals & authors:
            found.append(e)
    return found


def check(
    case: Case,
    out: list[dict[str, Any]],
    live: list[dict[str, Any]],
    published: list[dict[str, Any]],
) -> list[str]:
    problems = []
    text = "\n".join(" | ".join(flatten(r)) for r in out)
    for form in case.absent:
        if contiene(text, form) or form.lower() in text.lower():
            problems.append(f"leak: {case.spec['absent']} form #{case.absent.index(form)} survives")
    mode = case.spec["pseudonym"]
    known_ids = {e["entity_id"] for e in published}
    known_names = {(e.get("pseudonyms") or {}).get("name") for e in published}
    if mode == "same":
        want = expected_entity(published, case.forms)
        got = next((e for e in live if want and e["entity_id"] == want["entity_id"]), None)
        if want is None:
            problems.append("person not found in GET /entities")
        elif got is None:
            problems.append(f"expected {want['entity_id']} in response entities")
        elif got["pseudonyms"].get("name") != want["pseudonyms"].get("name"):
            problems.append(
                f"{want['entity_id']} pseudonym {got['pseudonyms'].get('name')!r} differs from "
                f"GET /entities {want['pseudonyms'].get('name')!r}"
            )
    elif mode in ("new", "distinct_new"):
        new = [
            e
            for e in named_entities(case, live)
            if e["entity_id"] not in known_ids
            and (e.get("pseudonyms") or {}).get("name") not in known_names
        ]
        need = 2 if mode == "distinct_new" else 1
        names = {(e.get("pseudonyms") or {}).get("name") for e in new}
        if len(new) < need or len(names) < need:
            problems.append(
                f"expected {need} new person entities, got {len(new)} ({len(names)} names)"
            )
    return problems


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--url", default="http://localhost:9000/v1")
    args = parser.parse_args()
    corpus = load_corpus(DirSource(args.raw))
    cases_file = json.loads(CASES.read_text(encoding="utf-8"))
    cases = [build(corpus, spec, cases_file) for spec in cases_file["cases"]]
    token = os.environ.get("ENTRYPOINT_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(base_url=args.url, headers=headers, timeout=90) as client:
        published = client.get("/entities").json()["entities"]
        batches: dict[str, list[Case]] = {}
        for case in cases:
            batches.setdefault(case.spec["source"], []).append(case)
        results = []
        for source, group in batches.items():
            records = [r for c in group for r in c.records]
            started = time.perf_counter()
            response = client.post("/anonymize", json={"source": source, "records": records})
            elapsed = time.perf_counter() - started
            print(
                f"batch {source}: {len(records)} records, "
                f"HTTP {response.status_code}, {elapsed:.2f}s"
            )
            body = response.json()
            by_id = {r["id"]: r for r in body.get("records", [])}
            for case in group:
                out = [by_id.get(r["id"], {}) for r in case.records]
                ids = {r["id"] for r in case.records}
                live = [e for e in body.get("entities", []) if ids & set(e.get("records", []))]
                if response.status_code != 200 or not all(out):
                    problems = [f"HTTP {response.status_code} or missing records"]
                else:
                    problems = check(case, out, live, published)
                results.append((case.spec["id"], case.spec["title"], problems))
    for cid, title, problems in results:
        print(f"{cid} {'PASS' if not problems else 'FAIL'}  {title}")
        for p in problems:
            print(f"      {p}")
    failed = sum(1 for _, _, p in results if p)
    print(f"{len(results) - failed}/{len(results)} PASS")


if __name__ == "__main__":
    main()
