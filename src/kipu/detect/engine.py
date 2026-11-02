"""Runs every detector over a group of surfaces (a chat thread or one doc/table row).

Context decides what the rules cannot see in a single string: which homonym a name refers
to, whether a bare first name is a participant, and who owns an unknown email or phone.
"""

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from kipu.audit.texto import CONFUSABLES, RX_CONFUSABLE
from kipu.detect.mention import IDENTIFIER_LABELS, Mention, resolve_overlaps
from kipu.detect.names import HandleMatcher, KeptMatcher, NameIndex, ngrams, tokenize
from kipu.detect.patterns import asks_for_cedula, digits_of, find_digit_runs, find_identifiers
from kipu.detect.quasi import QuasiLexicon
from kipu.directory.directory import EXCLUSIVE_TYPES, Directory, Person
from kipu.directory.names import fold
from kipu.directory.seed import norm_identifier
from kipu.identity.homonyms import REFERENCE, WINDOW, by_profile

ID_TYPES = {
    "EMAIL": "email",
    "CEDULA": "cedula",
    "PHONE": "phone",
    "PLATE": "plate",
    "PASSPORT": "passport",
    "BIRTH": "birth_iso",
    "ADDRESS": "address",
    "HANDLE": "handle",
    "ACCOUNT": "account",
}
# PG-: clients migrated from the Págalo portfolio keep their Págalo id.
RECORD_KEY = re.compile(r"\b((?:CL|PG)-\d{4,6}|KP-\d{3}|PQR-\d{4}-\d{4})\b")
RECORD_KEY_TYPES = {"CL-": "cliente_id", "PG-": "cliente_id", "KP-": "kp_id", "PQR": "radicado"}
STRONG_NAME_KINDS = ("full", "short", "handle")
LINKING_KINDS = ("full", "short", "external", "llm_full")
MAX_LINK_GAP = 40
SENTENCE_END = re.compile(r"[!?\n]|\.\s+[A-ZÁÉÍÓÚÑ]")
WEAK_NAME_KINDS = ("first", "surname")
# The text itself says two mentions of one name are two persons.
DISTINCT_CUE = re.compile(
    r"mismo nombre|hom[oó]nim[oa]s?|distint[oa]s|no (?:es|son) la misma|otra persona", re.I
)
# Table column headers that say whether the names below are clients or staff.
COLUMN_ROLES = {
    "cliente": "client",
    "clienta": "client",
    "titular": "client",
    "agente": "employee",
    "asesor": "employee",
    "asesora": "employee",
    "responsable": "employee",
    "empleado": "employee",
    "reporta a": "employee",
}


@dataclass
class Surface:
    record_id: str
    field: str
    text: str
    free_text: bool = False


@dataclass
class Group:
    """Records analysed together, with the persons known to take part in them (`related`:
    persons a row points to, such as an employee's manager)."""

    surfaces: list[Surface]
    authors: dict[str, str | None] = field(default_factory=dict)
    owner: str | None = None
    related: list[str] = field(default_factory=list)
    kind: str = "chat"
    prefer: str = ""
    scope: str = ""


# Extra mentions for one surface, given the group, its context and the rule mentions already
# resolved there (the LLM layer); they must not overlap the rule mentions.
ExtraMentions = Callable[[Surface, Group, list[str], list[Mention]], list[Mention]]
# Picks one homonym for a name no participant or identifier settles (the strong model).
HomonymJudge = Callable[[str, tuple[str, ...], Group], str | None]


class Detector:
    def __init__(
        self,
        directory: Directory,
        index: NameIndex,
        kept: KeptMatcher,
        handles: HandleMatcher | None = None,
        surrogates: set[str] | None = None,
        quasi: QuasiLexicon | None = None,
    ) -> None:
        self.directory = directory
        self.index = index
        self.kept = kept
        self.quasi = quasi or QuasiLexicon(directory.persons.values(), kept=kept.terms.values())
        self.handles = handles or HandleMatcher(directory.persons.values(), kept)
        self.surrogates = surrogates or set()
        self.extra: ExtraMentions | None = None
        self.judge: HomonymJudge | None = None

    def detect(self, group: Group) -> dict[tuple[str, str], list[Mention]]:
        raw = self._candidates(group)
        context = self._context(group, raw)
        resolved: dict[tuple[str, str], list[Mention]] = {}
        for surface in group.surfaces:
            key = (surface.record_id, surface.field)
            ours = self._surrogate_spans(surface.text)
            assigned = (self._assign(m, raw[key], context, group, surface) for m in raw[key])
            mentions = [m for m in assigned if m and not self._is_surrogate(m, ours)]
            mentions = self._split_homonyms(mentions, raw[key], surface.text)
            mentions += self._quasi(surface, context)
            chosen = resolve_overlaps(mentions)
            if self.extra:
                chosen = resolve_overlaps(chosen + self.extra(surface, group, context, chosen))
            owned: list[Mention] = []
            for m in chosen:
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
            found += [
                Mention(m.start(), m.end(), m.group(0), "REFERENCE", owner, value=m.group(0))
                for m in REFERENCE.finditer(view)
                if (owner := self.directory.references.get(m.group(0)))
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
        """Persons involved in the group, in order of strength of evidence: participants, then
        owners of identifiers in the group, then persons named unambiguously."""
        ordered: list[str] = [p for p in group.authors.values() if p]
        if group.owner:
            ordered.append(group.owner)
        ordered += group.related
        named: list[str] = []
        for mentions in raw.values():
            for m in mentions:
                pid = self._evidence_owner(m)
                if pid:
                    (named if m.label == "NAME" else ordered).append(pid)
        return list(dict.fromkeys(ordered + named))

    def _evidence_owner(self, m: Mention) -> str | None:
        if m.label == "HANDLE" and len(m.candidates) > 1:
            return None
        if m.label in ID_TYPES:
            return m.person_id or self.directory.owner_of(ID_TYPES[m.label], m.value)
        if m.label == "RECORD_KEY":
            return self.directory.owner_of(RECORD_KEY_TYPES[m.value[:3]], m.value)
        if m.label == "REFERENCE":
            return m.person_id
        if m.label == "SLACK":
            return self.directory.owner_of("uid", m.value)
        if m.label == "NAME" and m.kind in STRONG_NAME_KINDS and len(m.candidates) == 1:
            return m.candidates[0]
        return None

    def _preferred(self, candidates: tuple[str, ...], prefer: str) -> str | None:
        kind_ok = [p for p in candidates if self.directory.persons[p].kind == prefer]
        return kind_ok[0] if kind_ok else None

    def _assign(
        self,
        m: Mention,
        siblings: list[Mention],
        context: list[str],
        group: Group,
        surface: Surface,
    ) -> Mention | None:
        if m.label in ("RECORD_KEY", "REFERENCE"):
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
        role = column_role(surface.text, m.start)
        candidates = (
            tuple(p for p in m.candidates if self.directory.persons[p].kind == role) or m.candidates
        )
        participants = [p for p in group.authors.values() if p] + group.related
        if group.owner:
            participants.append(group.owner)
        chosen = (
            _choose(candidates, [p for _, p in nearby], participants)
            or self._profiled(m, surface.text, candidates)
            or _choose(candidates, [], context)
            or self._judged(m, group)
            or self._preferred(candidates, role or group.prefer)
            or (candidates[0] if candidates else None)
        )
        if m.kind in WEAK_NAME_KINDS and chosen not in context:
            chosen = self._sole_employee(m) if m.kind == "first" else None
            if chosen is None:
                return None
        return m.with_person(chosen)

    def _profiled(self, m: Mention, text: str, candidates: tuple[str, ...]) -> str | None:
        """A homonym no participant or nearby identifier settles: the one whose settled
        mentions elsewhere in the corpus share the words around this one."""
        if m.kind not in STRONG_NAME_KINDS or len(candidates) < 2 or not self.directory.profiles:
            return None
        window = fold(text[max(0, m.start - WINDOW) : m.end + WINDOW])
        name = " ".join(fold(m.text).split())
        return by_profile(candidates, window, name, self.directory.profiles)

    def _split_homonyms(
        self, mentions: list[Mention], siblings: list[Mention], text: str
    ) -> list[Mention]:
        """ "La PQR de Sofía Lemus con el extracto de Sofía Lemus: son dos clientas
        distintas con el mismo nombre": the mention nearest its evidence keeps the person,
        the others go to the other homonyms."""
        if not DISTINCT_CUE.search(text):
            return mentions
        by_name: dict[tuple[str, str], list[int]] = defaultdict(list)
        for i, m in enumerate(mentions):
            if m.label == "NAME" and m.person_id and len(m.candidates) > 1:
                by_name[(" ".join(fold(m.text).split()), m.person_id)].append(i)
        out = list(mentions)
        for (_, person_id), same in by_name.items():
            if len(same) < 2:
                continue
            kept = min(same, key=lambda i: (self._evidence_gap(mentions[i], siblings), i))
            others = [p for p in mentions[kept].candidates if p != person_id]
            for i, other in zip((j for j in same if j != kept), others, strict=False):
                out[i] = mentions[i].with_person(other)
        return out

    def _evidence_gap(self, m: Mention, siblings: list[Mention]) -> int:
        gaps = [
            _distance(s, m)
            for s in siblings
            if s is not m and self._evidence_owner(s) == m.person_id
        ]
        return min(gaps, default=10**9)

    def _judged(self, m: Mention, group: Group) -> str | None:
        if self.judge is None or m.kind not in STRONG_NAME_KINDS or len(m.candidates) < 2:
            return None
        return self.judge(m.text, m.candidates, group)

    def _surrogate_spans(self, text: str) -> list[tuple[int, int]]:
        """Spans of surrogate names we wrote earlier (an input copied from our output)."""
        if not self.surrogates:
            return []
        folded, tokens = tokenize(text)
        return [
            (tokens[i].start, tokens[j - 1].end)
            for i, j, window in ngrams(text, folded, tokens)
            if len(window) >= 2 and " ".join(window) in self.surrogates
        ]

    def _is_surrogate(self, m: Mention, ours: list[tuple[int, int]]) -> bool:
        """Leave values we wrote earlier as they are."""
        if not self.surrogates:
            return False
        if m.label == "NAME" and m.kind not in STRONG_NAME_KINDS:
            return any(start <= m.start and m.end <= end for start, end in ours)
        if m.label in ID_TYPES and m.label != "HANDLE" and not m.person_id:
            id_type = ID_TYPES[m.label]
            value = norm_identifier(id_type, m.value or m.text)
            return value in self.surrogates and self.directory.owner_of(id_type, value) is None
        return False

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
        named = None if owner else self._named_before(m, surface.text, owned)
        if named is not None:
            owner = self._link_to_name(m, id_type, named, owned, surface.record_id)
        elif not owner:
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

    def _named_before(self, m: Mention, text: str, owned: list[Mention]) -> int | None:
        """Index in `owned` of a person name just before a new identifier, in the same sentence
        ("Soy X, CC n", "X (cel n)", "me llamo X y mi cédula es n")."""
        if m.label == "HANDLE":
            return None
        for i in range(len(owned) - 1, -1, -1):
            name = owned[i]
            if name.end > m.start or name.label != "NAME":
                continue
            if not name.person_id or name.kind not in LINKING_KINDS:
                return None
            gap = text[name.end : m.start]
            if len(gap) <= MAX_LINK_GAP and not SENTENCE_END.search(gap):
                return i
            return None
        return None

    def _link_to_name(
        self, m: Mention, id_type: str, index: int, owned: list[Mention], record_id: str
    ) -> str:
        """The named person owns the identifier, unless it contradicts one they already have
        (another cédula): then the name belongs to a homonym, a new person."""
        name = owned[index]
        person = self.directory.persons[name.person_id]
        if self._contradicts(person, id_type, m.value):
            person = self.directory.external_person(name.text, f"{id_type}:{m.value}")
            owned[index] = name.with_person(person.person_id)
        if not self.directory.attach(person, id_type, m.value, record_id):
            return self.directory.external_person("", f"{id_type}:{m.value}").person_id
        return person.person_id

    def _contradicts(self, person: Person, id_type: str, raw: str) -> bool:
        if id_type not in EXCLUSIVE_TYPES:
            return False
        value = norm_identifier(id_type, raw)
        known = {norm_identifier(id_type, v) for v in person.identifiers.get(id_type, set())}
        known |= self.directory.registry.values_of(person.person_id, id_type)
        return bool(known) and value not in known

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
        persons = [self.directory.persons[p] for p in context if p in self.directory.persons]
        return self.quasi.scan(surface.text, persons)


def unconfuse(text: str) -> str:
    return RX_CONFUSABLE.sub(lambda m: CONFUSABLES[m.group(0)], text)


def column_role(text: str, pos: int) -> str:
    """ "client" or "employee" when `pos` sits in a markdown table column whose header says
    so ("Cliente", "Agente", "Reporta a")."""
    line_start = text.rfind("\n", 0, pos) + 1
    line_end = text.find("\n", pos)
    line = text[line_start : line_end if line_end != -1 else len(text)]
    if not line.lstrip().startswith("|"):
        return ""
    column = text[line_start:pos].count("|") - 1
    header_start = line_start
    while header_start > 0:
        previous = text.rfind("\n", 0, header_start - 1) + 1
        if not text[previous : header_start - 1].lstrip().startswith("|"):
            break
        header_start = previous
    if header_start == line_start:
        return ""
    header = text[header_start : text.find("\n", header_start)]
    cells = [c.strip() for c in header.strip().strip("|").split("|")]
    if column < 0 or column >= len(cells):
        return ""
    return COLUMN_ROLES.get(" ".join(fold(cells[column]).split()), "")


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
