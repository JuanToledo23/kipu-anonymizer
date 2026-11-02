"""Cross-source evidence for homonyms. The tie-break never sits next to the name (no city, no
job title); it lives in another source: a code that a transaction row ties to one client, or
the words around a mention of the same name that an identifier settles elsewhere."""

import re
from collections import defaultdict
from collections.abc import Iterable

from kipu.directory.directory import Directory, Person
from kipu.directory.names import fold, split_name
from kipu.ingest.records import TableRecord

# Hyphenated codes with a digit ("S-2608-01", "5832-0274"); record keys and dates excluded.
REFERENCE = re.compile(r"\b(?=[A-Z0-9-]*\d)[A-Z0-9]+(?:-[A-Z0-9]+)+\b")
NOT_REFERENCE = re.compile(r"^(?:(?:CL|PG|KP|PQR)-|\d{4}-\d{2}-\d{2}$)")
ANCHOR_TYPES = ("email", "handle", "cliente_id", "kp_id", "cedula", "phone", "radicado")
WINDOW = 200
WORD = re.compile(r"[a-z]{5,}")
MIN_SHARED_WORDS = 2
# Share of records a word may appear in and still tell two persons apart.
MAX_WORD_SHARE = 0.001


def reference_keys(tables: list[TableRecord], directory: Directory) -> dict[str, str]:
    """Codes in transaction descriptions that belong to exactly one client of this run."""
    owners: dict[str, set[str | None]] = defaultdict(set)
    for row in tables:
        if row.table != "transacciones":
            continue
        owner = directory.owner_of("cliente_id", row.columns.get("cliente_id", ""))
        for code in REFERENCE.findall(row.columns.get("descripcion", "")):
            if not NOT_REFERENCE.match(code):
                owners[code].add(owner)
    return {code: next(iter(o)) for code, o in owners.items() if len(o) == 1 and None not in o}


def homonym_groups(persons: Iterable[Person]) -> dict[str, set[str]]:
    """Folded full or short names held by more than one person with a master row."""
    holders: dict[str, set[str]] = defaultdict(set)
    for person in persons:
        if person.kind == "external":
            continue
        for name in person.names | {person.name.full}:
            if " " in name.strip():
                for form in (name, split_name(name).short):
                    holders[" ".join(fold(form).split())].add(person.person_id)
    return {name: ids for name, ids in holders.items() if len(ids) > 1}


def context_profiles(texts: list[str], directory: Directory) -> dict[str, frozenset[str]]:
    """For each homonym, the rare words around its mentions that one of its own identifiers
    settles (a name with its @handle, cédula or radicado within the window)."""
    groups = homonym_groups(directory.persons.values())
    if not groups:
        return {}
    folded = [fold(t) for t in texts]
    rare = rare_words(folded)
    pattern = re.compile(
        r"(?<![a-z0-9])(" + "|".join(re.escape(n) for n in sorted(groups, key=len)[::-1]) + ")"
        r"(?![a-z0-9])"
    )
    anchors = {pid: _anchors(directory.persons[pid]) for ids in groups.values() for pid in ids}
    profiles: dict[str, set[str]] = defaultdict(set)
    for text in folded:
        for m in pattern.finditer(text):
            window = text[max(0, m.start() - WINDOW) : m.end() + WINDOW]
            owners = [p for p in groups[m.group(1)] if any(a in window for a in anchors[p])]
            if len(owners) == 1:
                profiles[owners[0]] |= words_near(window, m.group(1)) & rare
    return {pid: frozenset(words) for pid, words in profiles.items()}


def rare_words(folded: list[str]) -> set[str]:
    counts: dict[str, int] = defaultdict(int)
    for text in folded:
        for word in {_stem(w) for w in WORD.findall(text)}:
            counts[word] += 1
    limit = max(3, int(len(folded) * MAX_WORD_SHARE))
    return {w for w, n in counts.items() if n <= limit}


def words_near(folded_window: str, name: str) -> set[str]:
    own = set(name.split())
    return {_stem(w) for w in WORD.findall(folded_window) if w not in own}


def by_profile(
    candidates: Iterable[str], folded_window: str, name: str, profiles: dict[str, frozenset[str]]
) -> str | None:
    """The one candidate whose profile shares enough words with this window, if the others
    share none."""
    words = words_near(folded_window, name)
    scores = {p: len(words & profiles.get(p, frozenset())) for p in candidates}
    best = [p for p, s in scores.items() if s >= MIN_SHARED_WORDS]
    if len(best) == 1 and all(s == 0 for p, s in scores.items() if p != best[0]):
        return best[0]
    return None


def _anchors(person: Person) -> set[str]:
    values = {
        fold(v) for t in ANCHOR_TYPES for v in person.identifiers.get(t, set()) if len(v) >= 5
    }
    values |= {f"<@{fold(u)}>" for u in person.identifiers.get("uid", set())}
    return values


def _stem(word: str) -> str:
    return word[:-1] if word.endswith("s") else word
