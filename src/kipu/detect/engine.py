"""Runs every detector over a group of surfaces (a chat thread or one doc/table row).

Context decides what the rules cannot see in a single string: which homonym a name refers
to, whether a bare first name is a participant, and who owns an unknown email or phone.
"""

import re
from dataclasses import dataclass, field, replace

from kipu.audit.texto import CONFUSABLES, RX_CONFUSABLE
from kipu.detect.mention import IDENTIFIER_LABELS, Mention, resolve_overlaps
from kipu.detect.names import HandleMatcher, KeptMatcher, NameIndex
from kipu.detect.patterns import asks_for_cedula, digits_of, find_digit_runs, find_identifiers
from kipu.directory.directory import Directory, Person
from kipu.directory.names import fold

ID_TYPES = {
    "EMAIL": "email",
    "CEDULA": "cedula",
    "PHONE": "phone",
    "PLATE": "plate",
    "PASSPORT": "passport",
    "BIRTH": "birth_iso",
    "ADDRESS": "address",
    "HANDLE": "handle",
}
RECORD_KEY = re.compile(r"\b(CL-\d{4,6}|KP-\d{3}|PQR-\d{4}-\d{4})\b")
RECORD_KEY_TYPES = {"CL-": "cliente_id", "KP-": "kp_id", "PQR": "radicado"}
STRONG_NAME_KINDS = ("full", "short", "handle")
WEAK_NAME_KINDS = ("first", "surname")


@dataclass
class Surface:
    record_id: str
    field: str
    text: str
    free_text: bool = False


@dataclass
class Group:
    """Records analysed together, with the persons known to take part in them."""

    surfaces: list[Surface]
    authors: dict[str, str | None] = field(default_factory=dict)
    owner: str | None = None
    kind: str = "chat"
    prefer: str = ""


class Detector:
    def __init__(
        self,
        directory: Directory,
        index: NameIndex,
        kept: KeptMatcher,
        handles: HandleMatcher | None = None,
    ) -> None:
        self.directory = directory
        self.index = index
        self.kept = kept
        self.handles = handles or HandleMatcher(directory.persons.values(), kept)

    def detect(self, group: Group) -> dict[tuple[str, str], list[Mention]]:
        raw = self._candidates(group)
        context = self._context(group, raw)
        resolved: dict[tuple[str, str], list[Mention]] = {}
        for surface in group.surfaces:
            key = (surface.record_id, surface.field)
            assigned = (self._assign(m, raw[key], context, group.prefer) for m in raw[key])
            mentions = [m for m in assigned if m]
            mentions += self._quasi(surface, context)
            owned: list[Mention] = []
            for m in resolve_overlaps(mentions):
                owned.append(self._own(m, surface, group, context, owned))
            resolved[key] = owned
        return resolved

    def _candidates(self, group: Group) -> dict[tuple[str, str], list[Mention]]:
        raw: dict[tuple[str, str], list[Mention]] = {}
        previous_text = ""
        for surface in group.surfaces:
            # Detection runs on a Latin view (look-alike letters map 1:1, so offsets hold).
            view = unconfuse(surface.text)
            requested = asks_for_cedula(previous_text)
            found = find_identifiers(view, requested)
            found += self._known_numbers(view)
            found += self.index.scan(view)
            found += self.handles.scan(view)
            found += self.kept.scan(view)
            found += [
                Mention(m.start(), m.end(), m.group(0), "RECORD_KEY", value=m.group(0))
                for m in RECORD_KEY.finditer(view)
            ]
            if view != surface.text:
                found = [replace(m, text=surface.text[m.start : m.end]) for m in found]
            raw[(surface.record_id, surface.field)] = found
            if surface.field == "text":
                previous_text = view
        return raw

    def _known_numbers(self, text: str) -> list[Mention]:
        """Digit runs equal to a known cédula or phone, even without context words."""
        found = []
        for runs in find_digit_runs(text):
            for start, end, run in runs:
                digits = digits_of(run)
                for label, id_type in (("CEDULA", "cedula"), ("PHONE", "phone")):
                    owner = self.directory.owner_of(id_type, digits)
                    if owner and len(digits) >= 6:
                        found.append(Mention(start, end, run, label, owner, value=digits))
        return found

    def _context(self, group: Group, raw: dict[tuple[str, str], list[Mention]]) -> list[str]:
        """Persons involved in the group, in order of strength of evidence."""
        ordered: list[str] = [p for p in group.authors.values() if p]
        if group.owner:
            ordered.append(group.owner)
        for mentions in raw.values():
            for m in mentions:
                pid = self._evidence_owner(m)
                if pid:
                    ordered.append(pid)
        return list(dict.fromkeys(ordered))

    def _evidence_owner(self, m: Mention) -> str | None:
        if m.label == "HANDLE" and len(m.candidates) > 1:
            return None
        if m.label in ID_TYPES:
            return m.person_id or self.directory.owner_of(ID_TYPES[m.label], m.value)
        if m.label == "RECORD_KEY":
            return self.directory.owner_of(RECORD_KEY_TYPES[m.value[:3]], m.value)
        if m.label == "SLACK":
            return self.directory.owner_of("uid", m.value)
        if m.label == "NAME" and m.kind in STRONG_NAME_KINDS and len(m.candidates) == 1:
            return m.candidates[0]
        return None

    def _preferred(self, candidates: tuple[str, ...], prefer: str) -> str | None:
        kind_ok = [p for p in candidates if self.directory.persons[p].kind == prefer]
        return kind_ok[0] if kind_ok else None

    def _assign(
        self, m: Mention, siblings: list[Mention], context: list[str], prefer: str
    ) -> Mention | None:
        if m.label == "RECORD_KEY":
            return None
        if m.label == "SLACK":
            return m.with_person(self.directory.owner_of("uid", m.value))
        if m.label == "HANDLE" and not m.person_id and m.candidates:
            # Shared handle: thread context first, else the newest person (latest corpus).
            return m.with_person(_choose(m.candidates, [], context) or m.candidates[-1])
        if m.label != "NAME" or m.kind == "external":
            return m
        if m.kind == "given_lower":
            # Lowercase given names only for persons taking part in this thread, doc or row.
            chosen = _choose(m.candidates, [], context)
            return m.with_person(chosen) if chosen else None
        nearby = sorted(
            (_distance(s, m), owner)
            for s in siblings
            if s is not m and (owner := self._evidence_owner(s)) and _distance(s, m) < 80
        )
        chosen = (
            _choose(m.candidates, [p for _, p in nearby], context)
            or self._preferred(m.candidates, prefer)
            or (m.candidates[0] if m.candidates else None)
        )
        if m.kind in WEAK_NAME_KINDS and chosen not in context:
            chosen = self._sole_employee(m) if m.kind == "first" else None
            if chosen is None:
                return None
        return m.with_person(chosen)

    def _sole_employee(self, m: Mention) -> str | None:
        """Staff are talked about by first name; take it when exactly one employee has it."""
        employees = [p for p in m.candidates if not self._is_client(p)]
        return employees[0] if len(employees) == 1 else None

    def _own(
        self,
        m: Mention,
        surface: Surface,
        group: Group,
        context: list[str],
        owned: list[Mention],
    ) -> Mention:
        if m.label == "NAME" and m.kind == "external":
            return m.with_person(self.directory.external_person(m.text, "").person_id)
        if m.label not in IDENTIFIER_LABELS or m.person_id:
            return m
        id_type = ID_TYPES[m.label]
        owner = self.directory.owner_of(id_type, m.value)
        if not owner:
            owner = self._attribute(m, surface, group, context, owned)
            person = (
                self.directory.persons.get(owner)
                if owner
                else self.directory.external_person("", f"{id_type}:{m.value}")
            )
            if person and not self.directory.attach(person, id_type, m.value, surface.record_id):
                person = self.directory.external_person("", f"{id_type}:{m.value}")
            owner = person.person_id if person else None
        return m.with_person(owner)

    def _attribute(
        self,
        m: Mention,
        surface: Surface,
        group: Group,
        context: list[str],
        owned: list[Mention],
    ) -> str | None:
        """Owner of an identifier the directory does not know yet."""
        preceding = [o for o in owned if o.label == "NAME" and o.person_id and o.end <= m.start]
        if preceding and m.start - preceding[-1].end <= 4:
            return preceding[-1].person_id
        if m.label == "HANDLE":
            return None
        if group.kind == "table":
            return group.owner
        author = group.authors.get(surface.record_id)
        if group.kind == "chat" and author and self._is_client(author):
            return author
        if preceding and m.start - preceding[-1].end <= 60:
            return preceding[-1].person_id
        if group.kind != "chat":
            return None
        return next((p for p in context if self._is_client(p)), None)

    def _is_client(self, person_id: str) -> bool:
        person = self.directory.persons.get(person_id)
        return person is not None and person.kind != "employee"

    def _quasi(self, surface: Surface, context: list[str]) -> list[Mention]:
        if not surface.free_text:
            return []
        found: list[Mention] = []
        folded = fold(surface.text)
        for pid in context:
            person = self.directory.persons.get(pid)
            if person is not None:
                found.extend(_quasi_for(person, surface.text, folded))
        return found


def unconfuse(text: str) -> str:
    return RX_CONFUSABLE.sub(lambda m: CONFUSABLES[m.group(0)], text)


def _distance(a: Mention, b: Mention) -> int:
    if a.end <= b.start:
        return b.start - a.end
    if b.end <= a.start:
        return a.start - b.end
    return 0


def _choose(candidates: tuple[str, ...], nearby: list[str], context: list[str]) -> str | None:
    for pool in (nearby, context):
        for pid in pool:
            if pid in candidates:
                return pid
    return None


def _quasi_for(person: Person, text: str, folded: str) -> list[Mention]:
    attrs = person.attrs
    found: list[Mention] = []
    barrio = attrs.get("barrio", "")
    if barrio:
        for start, end in _word_spans(folded, fold(barrio)):
            if text[start].isupper():
                found.append(Mention(start, end, text[start:end], "QUASI_BARRIO", person.person_id))
    for variant in occupation_variants(attrs.get("ocupacion", "")):
        for start, end in _word_spans(folded, variant):
            found.append(Mention(start, end, text[start:end], "QUASI_OCCUPATION", person.person_id))
    year = attrs.get("vinculacion", "")[:4]
    if year and found:
        for start, end in _word_spans(folded, year):
            found.append(Mention(start, end, text[start:end], "QUASI_YEAR", person.person_id))
    labels = {m.label for m in found}
    if len(labels) < 2:
        found = [m for m in found if m.label != "QUASI_YEAR"]
    return found


def occupation_variants(occupation: str) -> list[str]:
    occ = fold(occupation).strip()
    if not occ:
        return []
    head, _, rest = occ.partition(" ")
    swaps = {head}
    if head.endswith("ora"):
        swaps.add(head[:-1])
    elif head.endswith("or"):
        swaps.add(head + "a")
    elif head.endswith("a") and not head.endswith(("ista", "euta")):
        swaps.add(head[:-1] + "o")
    elif head.endswith("o"):
        swaps.add(head[:-1] + "a")
    return [f"{h} {rest}".strip() for h in sorted(swaps)]


def _word_spans(folded: str, needle: str) -> list[tuple[int, int]]:
    if not needle:
        return []
    pattern = re.compile(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])")
    return [(m.start(), m.end()) for m in pattern.finditer(folded)]
