"""Red-team sweep A1-A7 of an output dir against the raw run it was built from.

Usage:
  uv run python tests/redteam/sweep_output.py <output_dir> --raw <data/raw/<run>> --json <out.json>

Prints a PASS/FAIL line per check; the JSON holds every hit (record id, field, matched form).
The JSON contains original forms: keep it out of the repo.
"""

import argparse
import hashlib
import itertools
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from kipu.audit.auditor import flatten
from kipu.audit.texto import contiene, norm
from kipu.directory.names import GIVEN, fold, split_name
from kipu.ingest.parsers import RawCorpus, load_corpus
from kipu.ingest.source import DirSource

TEXT_COLUMNS = {"notas", "descripcion", "respuesta"}
NICKNAMES = (
    "Dani",
    "Pipe",
    "Cami",
    "Caro",
    "Juanpa",
    "Majo",
    "Nico",
    "Pao",
    "Vale",
    "Sebas",
    "Manu",
    "Alejo",
)
KEPT = (
    "Bancolombia",
    "Nequi",
    "Daviplata",
    "PSE",
    "Superfinanciera",
    "Kipu Pay",
    "Carulla",
    "Rappi",
)
PAIR = re.compile(r"\b([A-ZÁÉÍÓÚÑ][a-záéíóúñü]+) ([A-ZÁÉÍÓÚÑ][a-záéíóúñü]+)\b")
GAZETTEER = Path(__file__).resolve().parents[2] / "src/kipu/detect/gazetteer_co.txt"


def load_output(folder: Path) -> dict[str, tuple[str, dict[str, Any]]]:
    out = {}
    for source in ("chats", "docs", "tables"):
        for line in (folder / f"{source}.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                out[record["id"]] = (source, record)
    return out


def text_fields(source: str, record: dict[str, Any]) -> Iterator[tuple[str, str]]:
    """Free-text surfaces only: chat text, doc body, table notas/descripcion/respuesta."""
    if source == "chats":
        yield "text", record["text"]
    elif source == "docs":
        yield "body", record["body"]
    else:
        for column, value in record["columns"].items():
            if column in TEXT_COLUMNS:
                yield f"columns.{column}", value


def hidden_fields(source: str, record: dict[str, Any]) -> Iterator[tuple[str, str]]:
    if source == "docs":
        for key, value in record["metadata"].items():
            yield f"metadata.{key}", value
        body = record["body"]
        front = re.match(r"\A---\n(.*?)\n---\n", body, re.S)
        if front:
            yield "body.front_matter", front.group(1)
        for m in re.finditer(r"<!--(.*?)-->", body, re.S):
            yield "body.html_comment", m.group(1)
        for m in re.finditer(r"<meta[^>]*>", body):
            yield "body.meta", m.group(0)
        yield "filename", record["filename"]
    elif source == "chats":
        for i, att in enumerate(record["attachments"]):
            for key, value in att.items():
                yield f"attachments[{i}].{key}", " | ".join(flatten(value))
    elif record["table"] == "transacciones":
        yield "columns.descripcion", record["columns"]["descripcion"]


class Person:
    def __init__(self, name: str, key: str) -> None:
        self.name = name
        self.key = key
        self.forms: set[str] = {name}
        self.strong: set[str] = set()
        self.cliente_id = ""
        self.display = ""

    @property
    def first_given(self) -> str:
        return split_name(self.name).given[0]


def directory(corpus: RawCorpus) -> list[Person]:
    persons = []
    for t in corpus.tables:
        c = t.columns
        if t.table == "clientes":
            p = Person(c["nombre_completo"], t.id)
            p.cliente_id = c["cliente_id"]
            p.strong |= {c["num_doc"], c["correo"], c["celular"], c["cliente_id"]}
        elif t.table == "empleados":
            p = Person(c["nombre"], t.id)
            p.strong |= {c["documento"], c["correo"], c["celular"]}
        else:
            continue
        persons.append(p)
    for u in corpus.users:
        if u.get("is_bot") or not u.get("real_name"):
            continue
        prof = u.get("profile", {})
        p = Person(u["real_name"], u["id"])
        p.strong |= {u["name"], prof.get("email", ""), prof.get("phone", "")}
        p.display = prof.get("display_name", "")
        persons.append(p)
    for p in persons:
        parts = split_name(p.name)
        p.forms |= {parts.full, parts.short}
        p.strong.discard("")
        p.strong |= p.forms
    return persons


def raw_index(corpus: RawCorpus) -> tuple[dict[str, str], dict[str, str], dict[str, list[str]]]:
    texts, threads, by_thread = {}, {}, defaultdict(list)
    for r in corpus.records():
        texts[r.id] = norm(" | ".join(flatten(r.model_dump())))
        thread = getattr(r, "thread_id", None)
        if thread:
            threads[r.id] = thread
            by_thread[thread].append(r.id)
    return texts, threads, by_thread


def linked_records(
    p: Person, texts: dict[str, str], threads: dict[str, str], by_thread
) -> set[str]:
    """Raw records carrying a strong form of the person (substring prefilter, then contiene)."""
    needles = [(s, norm(s)) for s in p.strong if len(s) >= 4]
    direct = {rid for rid, t in texts.items() if any(n in t and contiene(t, s) for s, n in needles)}
    linked = set(direct)
    for rid in direct:
        if rid in threads:
            linked.update(by_thread[threads[rid]])
    return linked


def pseudonym_firsts(entities: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Per record, the folded tokens of pseudonym names declared for it (legit collisions)."""
    per_record: dict[str, set[str]] = defaultdict(set)
    for e in entities:
        name = (e.get("pseudonyms") or {}).get("name", "")
        for rid in e.get("records", []):
            per_record[rid] |= {fold(w) for w in name.split()}
    return per_record


def a1(persons, out, texts, threads, by_thread, entities) -> dict[str, list]:
    collisions = pseudonym_firsts(entities)
    leaks, ambiguous = [], []
    surfaces = [(rid, f, t) for rid, (s, r) in out.items() for f, t in text_fields(s, r)]
    seen: set[tuple[str, str]] = set()
    for p in persons:
        given = p.first_given
        if len(given) < 3:
            continue
        hits = [(rid, f) for rid, f, t in surfaces if contiene(t, given)]
        if not hits:
            continue
        linked = linked_records(p, texts, threads, by_thread)
        for rid, f in hits:
            if (rid, given, p.key) in seen:
                continue
            seen.add((rid, given, p.key))
            hit = {"record": rid, "field": f, "form": given, "person": p.key}
            if rid in linked and fold(given) not in collisions.get(rid, set()):
                leaks.append(hit)
            else:
                ambiguous.append(hit)
    return {"leaks": leaks, "ambiguous_count": len(ambiguous), "ambiguous_sample": ambiguous[:50]}


def a2(out, entities) -> list:
    pseudo = set()
    for e in entities:
        name = (e.get("pseudonyms") or {}).get("name", "")
        words = name.split()
        pseudo |= {fold(f"{a} {b}") for a, b in itertools.pairwise(words)}
    gaz = {
        fold(line.strip())
        for line in GAZETTEER.read_text().splitlines()
        if line and not line.startswith("#")
    }
    counts: Counter = Counter()
    where: dict[str, list[str]] = defaultdict(list)
    for rid, (s, r) in out.items():
        for _, t in text_fields(s, r):
            for m in PAIR.finditer(t):
                pair = m.group(0)
                if (
                    fold(pair) in pseudo
                    or fold(pair) in gaz
                    or any(fold(w) in gaz for w in pair.split())
                ):
                    continue
                counts[pair] += 1
                where[pair].append(rid)
    return [
        {
            "pair": pair,
            "n": n,
            "given_name_first": fold(pair.split()[0]) in GIVEN,
            "records": where[pair][:5],
        }
        for pair, n in counts.most_common(30)
    ]


def a3(persons, out, raw_out) -> list:
    forms = set(NICKNAMES)
    forms |= {p.display for p in persons if p.display and fold(p.display) != fold(p.first_given)}
    hits = []
    for rid, (s, r) in out.items():
        for f, t in [*text_fields(s, r), *hidden_fields(s, r)]:
            for form in forms:
                if len(form) >= 3 and contiene(t, form):
                    hits.append(
                        {
                            "record": rid,
                            "field": f,
                            "form": form,
                            "in_raw": contiene(raw_out.get(rid, ""), form),
                        }
                    )
    return hits


def person_forms(p: Person) -> set[str]:
    forms = set(p.strong)
    for s in list(p.strong):
        if "@" in s:
            forms.add(s.split("@")[0])
    return {f for f in forms if len(f) >= 4}


def a4(persons, out) -> list:
    surfaces = [(rid, f, t) for rid, (s, r) in out.items() for f, t in hidden_fields(s, r)]
    whole = "\n".join(t for _, _, t in surfaces)
    hits = []
    for p in persons:
        for form in person_forms(p):
            if form.startswith("CL-") or not contiene(whole, form):
                continue
            hits += [
                {"record": rid, "field": f, "form": form}
                for rid, f, t in surfaces
                if contiene(t, form)
            ][:5]
    return hits


def a5(persons, out_text: str) -> list:
    low = out_text.lower()
    hits = []
    for p in persons:
        for value in p.strong:
            if value.startswith("CL-") or " " in value:
                continue
            variants = {value, value.lower(), re.sub(r"\D", "", value)} - {""}
            for v in variants:
                for algo in ("md5", "sha1", "sha256"):
                    h = hashlib.new(algo, v.encode()).hexdigest()
                    if h in low or h[:16] in low:
                        hits.append({"form": f"{algo}({p.key})"})
    return hits


def a6(corpus: RawCorpus, out) -> list:
    hits = []
    surfaces = []
    for rid, (s, r) in out.items():
        if s == "chats":
            surfaces.append((rid, r["text"]))
        elif s == "tables" and r["table"] in ("clientes", "pqrs"):
            cols = r["columns"]
            surfaces.append((rid, " | ".join(cols.get(c, "") for c in TEXT_COLUMNS)))
    for t in corpus.tables:
        if t.table != "clientes":
            continue
        c = t.columns
        year = c["fecha_vinculacion"][:4]
        for rid, text in surfaces:
            if (
                contiene(text, c["barrio"])
                and contiene(text, c["ocupacion"])
                and re.search(rf"\b{year}\b", text)
            ):
                hits.append({"record": rid, "cliente": t.id})
    return hits


def a7(corpus: RawCorpus, out, raw_out) -> list:
    cities = {t.columns["ciudad"] for t in corpus.tables if t.table == "clientes"}
    terms = [*KEPT, *sorted(cities)]
    hits = []
    for rid, raw in raw_out.items():
        if rid not in out:
            continue
        o = " | ".join(flatten(out[rid][1]))
        for term in terms:
            pat = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)")
            n_raw, n_out = len(pat.findall(raw)), len(pat.findall(o))
            if n_raw > n_out:
                hits.append({"record": rid, "form": term, "raw": n_raw, "out": n_out})
    return hits


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args()
    corpus = load_corpus(DirSource(args.raw))
    out = load_output(args.output)
    entities = json.loads((args.output / "entities.json").read_text(encoding="utf-8"))["entities"]
    persons = directory(corpus)
    texts, threads, by_thread = raw_index(corpus)
    raw_out = {r.id: " | ".join(flatten(r.model_dump())) for r in corpus.records()}
    out_text = "\n".join(" | ".join(flatten(r)) for _, r in out.values())
    results = {
        "A1": a1(persons, out, texts, threads, by_thread, entities),
        "A2": a2(out, entities),
        "A3": a3(persons, out, raw_out),
        "A4": a4(persons, out),
        "A5": a5(persons, out_text),
        "A6": a6(corpus, out),
        "A7": a7(corpus, out, raw_out),
    }
    args.json.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    counts = {
        "A1": len(results["A1"]["leaks"]),
        "A2": sum(1 for x in results["A2"] if x["given_name_first"]),
        "A3": len(results["A3"]),
        "A4": len(results["A4"]),
        "A5": len(results["A5"]),
        "A6": len(results["A6"]),
        "A7": len(results["A7"]),
    }
    print(
        f"persons={len(persons)} records={len(out)} A1 ambiguous={results['A1']['ambiguous_count']}"
    )
    for check, n in counts.items():
        print(f"{check} hits={n} {'FAIL' if n else 'PASS'}")


if __name__ == "__main__":
    main()
