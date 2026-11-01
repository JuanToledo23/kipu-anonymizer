"""Judge-like identity checker: the "consistencia" and "homónimos" families, replayed over every
person of the registry instead of the few the public tests name.

  a  per person and original form (full/short name, cédula, email, phone, handle, display
     name; the given name only inside threads the person speaks in):
       a1 a raw record with the form is missing from the entity's `records`
       a2 the record's output has none of the entity's name forms (full, given, file token);
          `a2-id` when the raw record only had an identifier, which no rule can turn into a name
       a3 instead, another entity's name pseudonym is there (bare-mention misattribution)
       a4 the person has forms in the raw corpus but no entity
     plus a5: a name pseudonym in an output record where the raw record has no form of its
     entity, is not authored by it and is not its master row
  b  b1 a pseudonym value declared by two entities; b2 an original declared by two entities
  c  persons sharing a normalized full or short name: c1 same `name` pseudonym; c2 a raw
     record with the shared name whose owner (author, master row, thread, identifiers) is one
     of them, rendered with another homonym's pseudonym or with neither
  d  persons with several emails/phones/cédulas in the corpus: a surrogate written in the output
     that entities.json does not declare
  e  users.json clients without a clientes row, with the rows matching their name

Usage:
  uv run python scripts/judge_identity.py <output_dir> --raw <data/raw/<run>> \
      --registry <registry.sqlite> [--limit N]
"""

import argparse
import bisect
import json
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import regex

from kipu.audit.auditor import record_text
from kipu.audit.texto import (
    RX_ASCII_LETRA,
    contiene,
    digitos_compactos,
    norm,
    partir,
    solo_digitos,
)
from kipu.directory.directory import Directory, Person
from kipu.directory.names import GIVEN, fold, split_name, strip_accents
from kipu.directory.registry import Registry
from kipu.ingest.parsers import RawCorpus, load_corpus
from kipu.ingest.source import DirSource
from kipu.pipeline import build_directory

IDENTIFIER_FORMS = ("cedula", "email", "phone")
OWNER_IDS = ("cedula", "email", "phone", "cliente_id", "kp_id", "radicado")
MULTI_FIELDS = ("email", "phone", "cedula")
MIN_TEXT_FORM = 5
CODE = regex.compile(r"\b(?=[A-Z0-9-]*\d)[A-Z0-9]+(?:-[A-Z0-9]+)+\b")
COLUMN_KINDS = {
    "cliente": "client",
    "clienta": "client",
    "agente": "employee",
    "reporta a": "employee",
}
DISTINCT_CUE = regex.compile(r"mismo nombre|hom[oó]nim|distint[oa]s|otra persona", regex.I)
WORD = regex.compile(r"[\p{L}\p{N}]+")


class TextIndex:
    """Records containing a form under `contiene`, prefiltered by a substring search over the
    whole normalized corpus (a necessary condition of every `contiene` branch)."""

    def __init__(self, texts: dict[str, str]) -> None:
        self.texts = texts
        self.ids = list(texts)
        self.normed, self.norm_starts = _joined(norm(texts[r]) for r in self.ids)
        self.compact, self.compact_starts = _joined(digitos_compactos(texts[r]) for r in self.ids)
        self.cache: dict[str, frozenset[str]] = {}

    def find(self, form: str) -> frozenset[str]:
        if form not in self.cache:
            self.cache[form] = frozenset(
                rid for rid in self._candidates(form) if contiene(self.texts[rid], form)
            )
        return self.cache[form]

    def _candidates(self, form: str) -> set[str]:
        digits = solo_digitos(form)
        if len(digits) >= 6 and len(digits) >= 0.6 * len(form) and not RX_ASCII_LETRA.search(form):
            key = digits[2:] if len(digits) == 12 and digits.startswith("57") else digits
            return self._scan(self.compact, self.compact_starts, key)
        words = partir(norm(form))
        if not words:
            return set()
        return self._scan(self.normed, self.norm_starts, max(words, key=len))

    def _scan(self, haystack: str, starts: list[int], key: str) -> set[str]:
        hits, pos = set(), haystack.find(key)
        while pos != -1:
            hits.add(self.ids[bisect.bisect_right(starts, pos) - 1])
            pos = haystack.find(key, pos + 1)
        return hits


def _joined(parts: Iterable[str]) -> tuple[str, list[int]]:
    starts, chunks, offset = [], [], 0
    for part in parts:
        starts.append(offset)
        chunks.append(part)
        offset += len(part) + 1
    return "\0".join(chunks), starts


@dataclass
class Form:
    kind: str
    value: str


@dataclass
class World:
    corpus: RawCorpus
    directory: Directory
    registry: Registry
    entities: dict[str, dict]
    raw: TextIndex
    out: TextIndex
    author: dict[str, str] = field(default_factory=dict)
    row_owner: dict[str, str] = field(default_factory=dict)
    thread: dict[str, str] = field(default_factory=dict)
    chat_text: dict[str, str] = field(default_factory=dict)
    codes: dict[str, str] = field(default_factory=dict)
    speakers: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    thread_records: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    def name_forms(self, person_id: str) -> list[str]:
        name = (self.entities.get(person_id, {}).get("pseudonyms") or {}).get("name")
        if not name:
            return []
        parts = split_name(name)
        return [name, parts.first, "_".join(strip_accents(name).lower().split())]

    def has_name(self, person_id: str, record_id: str) -> bool:
        text = self.out.texts.get(record_id, "")
        return any(contiene(text, f) for f in self.name_forms(person_id))

    def owners(self, record_id: str) -> set[str]:
        return {p for p in (self.author.get(record_id), self.row_owner.get(record_id)) if p}


def person_forms(person: Person) -> list[Form]:
    forms: list[Form] = []
    names = {n for n in person.names | {person.name.full} if " " in n.strip()}
    for name in sorted(names):
        forms.append(Form("full", name))
        short = split_name(name).short
        if short != name:
            forms.append(Form("short", short))
    for kind in IDENTIFIER_FORMS:
        forms += [Form(kind, v) for v in sorted(person.identifiers.get(kind, set()))]
    for handle in sorted(person.identifiers.get("handle", set())):
        if len(handle) >= MIN_TEXT_FORM and fold(handle) not in GIVEN:
            forms.append(Form("handle", handle))
    forms += [Form("mention", f"<@{uid}>") for uid in sorted(person.identifiers.get("uid", set()))]
    display = person.attrs.get("display_name", "")
    if (
        len(display) >= MIN_TEXT_FORM
        and fold(display) not in GIVEN
        and fold(display) != fold(person.name.first)
    ):
        forms.append(Form("display", display))
    return forms


def build_world(output: Path, raw_dir: Path, registry: Registry) -> World:
    corpus = load_corpus(DirSource(raw_dir))
    directory, authors = build_directory(corpus, registry)
    out_records = {}
    for source in ("chats", "docs", "tables"):
        for line in (output / f"{source}.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                out_records[record["id"]] = record_text(record)
    entities = json.loads((output / "entities.json").read_text(encoding="utf-8"))["entities"]
    world = World(
        corpus,
        directory,
        registry,
        {e["entity_id"]: e for e in entities if not e.get("kept")},
        TextIndex({r.id: record_text(r.model_dump()) for r in corpus.records()}),
        TextIndex(out_records),
    )
    world.author = {rid: pid for rid, pid in authors.items() if pid}
    for chat in corpus.chats:
        world.thread[chat.id] = chat.thread_id
        world.chat_text[chat.id] = chat.text
        world.thread_records[chat.thread_id].append(chat.id)
        if chat.id in world.author:
            world.speakers[chat.thread_id].add(world.author[chat.id])
    code_owners: dict[str, set[str | None]] = defaultdict(set)
    for row in corpus.tables:
        if row.table == "transacciones":
            owner = directory.owner_of("cliente_id", row.columns.get("cliente_id", ""))
            for code in CODE.findall(row.columns.get("descripcion", "")):
                code_owners[code].add(owner)
        if row.table == "empleados":
            owner = directory.owner_of("kp_id", row.columns.get("id", ""))
        else:
            owner = directory.owner_of("cliente_id", row.columns.get("cliente_id", ""))
        if owner:
            world.row_owner[row.id] = owner
    world.codes = {c: o.pop() for c, o in code_owners.items() if len(o) == 1 and None not in o}
    return world


def shared_name_keys(directory: Directory) -> dict[str, set[str]]:
    """Normalized full/short names held by more than one person."""
    holders: dict[str, set[str]] = defaultdict(set)
    for person in directory.persons.values():
        for form in person_forms(person):
            if form.kind in ("full", "short", "display"):
                holders[norm(form.value)].add(person.person_id)
    return {k: v for k, v in holders.items() if len(v) > 1}


def check_consistency(world: World, shared: dict[str, set[str]]) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    for person in world.directory.persons.values():
        pid = person.person_id
        entity = world.entities.get(pid)
        hits: dict[str, set[str]] = defaultdict(set)
        for form in person_forms(person):
            if norm(form.value) in shared:
                continue
            for rid in world.raw.find(form.value):
                hits[rid].add(form.kind)
        hits.update(_given_in_threads(world, person, hits))
        if not hits:
            continue
        if entity is None:
            findings.append(("a4", f"{pid} has forms in {len(hits)} raw records, no entity"))
            continue
        records = set(entity["records"])
        for rid, kinds in sorted(hits.items()):
            label = "/".join(sorted(kinds))
            if rid not in records:
                findings.append(("a1", f"{pid} {rid} [{label}] not in records"))
            if world.has_name(pid, rid) or not world.name_forms(pid):
                continue
            if not kinds & {"full", "short", "given", "display"}:
                findings.append(("a2-id", f"{pid} {rid} [{label}] only an identifier"))
                continue
            others = [
                o
                for o in world.entities
                if o != pid and o not in world.owners(rid) and _has_full(world, o, rid)
            ]
            if others:
                findings.append(("a3", f"{pid} {rid} [{label}] rendered as {others}"))
            else:
                findings.append(("a2", f"{pid} {rid} [{label}] name pseudonym absent"))
    return findings


def _given_in_threads(
    world: World, person: Person, hits: dict[str, set[str]]
) -> dict[str, set[str]]:
    """Given-name mentions ("Hecho, Benjamín") in threads the person speaks in."""
    given = person.name.first
    if not given or person.kind == "external":
        return {}
    extra: dict[str, set[str]] = {}
    surnames = {fold(t) for t in person.name.surnames}
    for thread, speakers in world.speakers.items():
        if person.person_id not in speakers:
            continue
        others = {
            s
            for s in speakers
            if s != person.person_id and fold(world.directory.persons[s].name.first) == fold(given)
        }
        if others:
            continue
        for rid in world.thread_records[thread]:
            if (
                rid not in hits
                and world.author.get(rid) != person.person_id
                and _bare_given(world.chat_text[rid], given, surnames)
            ):
                extra[rid] = {"given"}
    return extra


def _bare_given(text: str, given: str, surnames: set[str]) -> bool:
    """`given` as a name of its own, not inside someone else's ("Juan Carlos Salazar")."""
    words = WORD.findall(text)
    folded = [fold(w) for w in words]
    target = fold(given).split()
    n = len(target)
    for i in range(len(words) - n + 1):
        if folded[i : i + n] != target:
            continue
        before = words[i - 1] if i else ""
        after = words[i + n] if i + n < len(words) else ""
        if before[:1].isupper() and fold(before) in GIVEN:
            continue
        if after[:1].isupper() and fold(after) not in surnames:
            continue
        return True
    return False


def _has_full(world: World, person_id: str, record_id: str) -> bool:
    name = (world.entities[person_id].get("pseudonyms") or {}).get("name", "")
    return " " in name and record_id in world.out.find(name)


def check_foreign(world: World) -> list[tuple[str, str]]:
    """a5: a name pseudonym with no trace of its person in the raw record."""
    findings = []
    for pid, entity in world.entities.items():
        name = (entity.get("pseudonyms") or {}).get("name", "")
        person = world.directory.persons.get(pid)
        if " " not in name or person is None:
            continue
        evidence = [f.value for f in person_forms(person)]
        evidence += [v for t in OWNER_IDS for v in person.identifiers.get(t, set())]
        evidence += [f"<@{uid}>" for uid in person.identifiers.get("uid", set())]
        if person.name.first:
            evidence.append(person.name.first)
        evidence += list(entity.get("originals", []))
        for rid in sorted(world.out.find(name)):
            if pid in world.owners(rid):
                continue
            raw = world.raw.texts.get(rid, "")
            if not any(contiene(raw, e) for e in evidence if e):
                findings.append(("a5", f"{pid} '{name}' in {rid}, no form of {pid} in raw"))
    return findings


def check_uniqueness(world: World) -> list[tuple[str, str]]:
    values: dict[tuple[str, str], set[str]] = defaultdict(set)
    originals: dict[str, set[str]] = defaultdict(set)
    for pid, entity in world.entities.items():
        for kind, value in (entity.get("pseudonyms") or {}).items():
            key = "name" if kind in ("name", "handle") else kind
            values[(key, norm(value))].add(pid)
        for form in entity.get("originals", []):
            originals[norm(form)].add(pid)
    findings = [
        ("b1", f"{kind} '{value}' declared by {sorted(ids)}")
        for (kind, value), ids in sorted(values.items())
        if len(ids) > 1
    ]
    findings += [
        ("b2", f"original '{form}' declared by {sorted(ids)}")
        for form, ids in sorted(originals.items())
        if len(ids) > 1
    ]
    return findings


def check_homonyms(world: World, shared: dict[str, set[str]]) -> list[tuple[str, str]]:
    findings = []
    for key, group in sorted(shared.items()):
        form = next(
            (
                f.value
                for pid in sorted(group)
                for f in person_forms(world.directory.persons[pid])
                if f.kind in ("full", "short") and norm(f.value) == key
            ),
            None,
        )
        if form is None:
            continue
        names = {
            pid: (world.entities.get(pid, {}).get("pseudonyms") or {}).get("name") for pid in group
        }
        declared = [n for n in names.values() if n]
        if len(set(declared)) < len(declared):
            findings.append(("c1", f"'{key}' {sorted(group)} share a pseudonym {names}"))
        for rid in sorted(world.raw.find(form)):
            findings += _homonym_lines(world, key, form, group, names, rid)
    return findings


def _homonym_lines(
    world: World, key: str, form: str, group: set[str], names: dict[str, str | None], rid: str
) -> list[tuple[str, str]]:
    """Each raw line with the shared name against the same output line (replacement keeps
    line breaks): owner by identifiers on the line, then its table column, then the record."""
    raw_lines = world.raw.texts[rid].split("\n")
    out_lines = world.out.texts.get(rid, "").split("\n")
    if len(raw_lines) != len(out_lines):
        raw_lines, out_lines = [world.raw.texts[rid]], [world.out.texts.get(rid, "")]
    findings = []
    offset = 0
    for raw_line, out_line in zip(raw_lines, out_lines, strict=True):
        start, offset = offset, offset + len(raw_line) + 1
        if not contiene(raw_line, form):
            continue
        used = [p for p in sorted(group) if _name_in(world, p, out_line)]
        if len(norm(raw_line).split(key)) > 2 and DISTINCT_CUE.search(raw_line) and len(used) < 2:
            findings.append(("c3", f"'{key}' {rid} two persons by the text, rendered as {used}"))
            continue
        owner = (
            _line_owner(world, group, raw_line)
            or _column_owner(world, group, world.raw.texts[rid], start, raw_line, form)
            or _homonym_owner(world, group, rid)
        )
        if owner is None:
            findings.append(("c?", f"'{key}' {rid} owner undetermined, rendered as {used}"))
            continue
        wrong = [p for p in used if p != owner]
        if wrong:
            findings.append(("c2", f"'{key}' {rid} belongs to {owner}, rendered as {wrong}"))
        elif owner not in used and names.get(owner):
            findings.append(("c2", f"'{key}' {rid} belongs to {owner}, its pseudonym absent"))
    return findings


def _name_in(world: World, person_id: str, text: str) -> bool:
    name = (world.entities.get(person_id, {}).get("pseudonyms") or {}).get("name", "")
    return " " in name and contiene(text, name)


def _line_owner(world: World, group: set[str], line: str) -> str | None:
    hits = {world.codes[c] for c in CODE.findall(line) if world.codes.get(c) in group}
    for pid in group:
        person = world.directory.persons[pid]
        values = [v for t in OWNER_IDS for v in person.identifiers.get(t, set()) if len(v) >= 5]
        values += [f"<@{u}>" for u in person.identifiers.get("uid", set())]
        values += [n for n in person.names if norm(n) not in _shared_cache(world)]
        if any(contiene(line, v) for v in values):
            hits.add(pid)
    return hits.pop() if len(hits) == 1 else None


def _column_owner(
    world: World, group: set[str], text: str, start: int, line: str, form: str
) -> str | None:
    """A markdown table cell under a "Cliente"/"Agente"-like header."""
    if not line.lstrip().startswith("|"):
        return None
    lines = text[:start].split("\n")[:-1]
    header = line
    while lines and lines[-1].lstrip().startswith("|"):
        header = lines.pop()
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    heads = [norm(c.strip()) for c in header.strip().strip("|").split("|")]
    column = next((i for i, c in enumerate(cells) if contiene(c, form)), None)
    if header is line or column is None or column >= len(heads):
        return None
    role = COLUMN_KINDS.get(heads[column], "")
    kinds = [p for p in group if world.directory.persons[p].kind == role]
    return kinds[0] if len(kinds) == 1 else None


def _homonym_owner(world: World, group: set[str], record_id: str) -> str | None:
    if record_id.startswith("empleados:"):  # only `reporta_a` names someone else
        staff = [p for p in group if world.directory.persons[p].kind == "employee"]
        return staff[0] if len(staff) == 1 else None
    direct = world.owners(record_id) & group
    if len(direct) == 1:
        return direct.pop()
    thread = world.thread.get(record_id)
    if thread:
        speakers = world.speakers[thread] & group
        if len(speakers) == 1:
            return speakers.pop()
        texts = [world.raw.texts[r] for r in world.thread_records[thread]]
    else:
        texts = [world.raw.texts[record_id]]
    text = "\n".join(texts)
    by_ids = set()
    for pid in group:
        person = world.directory.persons[pid]
        values = [v for t in OWNER_IDS for v in person.identifiers.get(t, set())]
        unique_names = [n for n in person.names if norm(n) not in _shared_cache(world)]
        if any(contiene(text, v) for v in values + unique_names):
            by_ids.add(pid)
    return by_ids.pop() if len(by_ids) == 1 else None


_SHARED: dict[int, set[str]] = {}


def _shared_cache(world: World) -> set[str]:
    if id(world) not in _SHARED:
        _SHARED[id(world)] = set(shared_name_keys(world.directory))
    return _SHARED[id(world)]


def check_surrogates(world: World) -> list[tuple[str, str]]:
    findings = []
    rows = world.registry.conn.execute(
        "SELECT person_id, field, original_norm, value FROM surrogates"
    ).fetchall()
    for pid, field_name, original, value in rows:
        if field_name not in MULTI_FIELDS or pid not in world.entities:
            continue
        declared = (world.entities[pid].get("pseudonyms") or {}).get(field_name)
        if not original or value == declared or not world.raw.find(original):
            continue
        written = sorted(world.out.find(value))
        if written:
            findings.append(
                ("d", f"{pid} {field_name} '{value}' (for {original}) undeclared, in {written[:4]}")
            )
    return findings


def check_unlinked(world: World) -> list[tuple[str, str]]:
    findings = []
    clientes = [r for r in world.corpus.tables if r.table == "clientes"]
    for user in world.corpus.users:
        profile = user.get("profile", {})
        if user.get("is_bot") or profile.get("title") != "Cliente":
            continue
        owner = world.directory.owner_of("uid", user["id"])
        person = world.directory.persons.get(owner or "")
        if person is not None and person.identifiers.get("cliente_id"):
            continue
        name = user.get("real_name") or profile.get("real_name") or ""
        keys = {norm(name), norm(split_name(name).short)}
        matches = [
            r.id
            for r in clientes
            if keys
            & {
                norm(r.columns["nombre_completo"]),
                norm(split_name(r.columns["nombre_completo"]).short),
            }
        ]
        ids = {
            t: v
            for t, v in (("email", profile.get("email", "")), ("phone", profile.get("phone", "")))
            if v
        }
        records = sorted(rid for rid, a in world.author.items() if a == owner) if owner else []
        findings.append(
            (
                "e",
                f"{user['id']} '{name}' → {owner} ids={sorted(ids)} "
                f"authored {len(records)} records; clientes matching the name: {matches}",
            )
        )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=15, help="examples printed per check")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "registry.sqlite"
        shutil.copy(args.registry, copy)
        registry = Registry(copy)
        world = build_world(args.output, args.raw, registry)
        shared = shared_name_keys(world.directory)
        findings = (
            check_consistency(world, shared)
            + check_foreign(world)
            + check_uniqueness(world)
            + check_homonyms(world, shared)
            + check_surrogates(world)
            + check_unlinked(world)
        )
        registry.close()
    counts = Counter(code for code, _ in findings)
    printed: Counter[str] = Counter()
    for code, line in findings:
        if printed[code] < args.limit:
            print(f"{code:<5} {line}")
        printed[code] += 1
    print("homonym groups:", len(shared))
    print("violations:", dict(sorted(counts.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
