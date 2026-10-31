"""Verification of model candidates: the model proposes strings, the code decides.

A candidate becomes a span only if its text is found literally in the unit (offsets computed
here), it is not a kept term, not already covered by the rules, and its value has the shape of
its label. Persons need both models, or one model and a name-shaped span; anything else in
doubt goes to the strong model, whose verdict is checked against the same rules.
"""

import asyncio
import datetime as dt
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from kipu.detect.names import TOKEN, KeptMatcher, NameIndex
from kipu.directory.names import fold
from kipu.llm.client import LLMClient
from kipu.llm.extract import (
    KEEP_LABELS,
    SECURITY,
    Candidate,
    Item,
    Unit,
    render_unit,
)

log = logging.getLogger(__name__)

Key = tuple[str, str]
IDENTIFIER_SHAPED = ("PERSONAL_ADDRESS", "BIRTH_DATE", "PASSPORT", "ACCOUNT", "PLATE", "EMAIL")
MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}
BIRTH_WORDS = re.compile(r"(\d{1,2})\s+de\s+([a-z]+)\s+(?:de|del)\s+(\d{4})")
AGE_NUMBER = re.compile(r"\b(\d{1,3})\b")
EMAIL_PLAIN = re.compile(r"[a-z0-9._%+\-]+@[a-z0-9\-]+(?:\.[a-z0-9\-]+)+")
RECORD_CODE = re.compile(r"\b(?:CL|KP|TX|PQR)-\d", re.I)
NOT_PERSONS = frozenset({"kipi", "kipu", "kipu pay"})
TITLES = frozenset(
    {
        "senor",
        "senora",
        "senorita",
        "sr",
        "sra",
        "srta",
        "don",
        "dona",
        "dr",
        "dra",
        "doctor",
        "doctora",
        "ing",
        "lic",
        "licenciado",
        "licenciada",
        "estimado",
        "estimada",
        "cliente",
        "clienta",
        "usuario",
        "usuaria",
        "asesor",
        "asesora",
        "agente",
    }
)

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdicts"],
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "personal"],
                "properties": {"id": {"type": "integer"}, "personal": {"type": "boolean"}},
            },
        }
    },
}
ADJUDICATE_SYSTEM = f"""Eres el juez final de un detector de datos personales en textos en \
español de Colombia (soporte de Kipu Pay, una billetera digital).

{SECURITY}

Dos detectores no coinciden sobre las menciones listadas después de los datos. Para cada id \
decide personal=true si, en ese texto, es un dato personal: el nombre, apodo o apellido de una \
persona natural (cliente, empleado o tercero), su dirección, fecha de nacimiento, edad, \
pasaporte, número de cuenta, placa, correo o una frase que la identifique por ocupación, barrio \
o año. Decide personal=false si es una organización, comercio, marca, lugar, figura pública, \
una palabra común o un código que no es de una persona. Responde un veredicto por id."""


@dataclass(frozen=True)
class Span:
    """An accepted model mention, in the coordinates of one record surface."""

    record_id: str
    field: str
    start: int
    end: int
    text: str
    label: str
    gender: str = "unknown"
    role: str = "unknown"
    source: str = "both"
    person_id: str | None = None

    @property
    def key(self) -> Key:
        return (self.record_id, self.field)


@dataclass
class Group:
    """Votes for one (text, label) in one unit."""

    candidate: Candidate
    models: list[str]
    sources: list[str]


@dataclass
class Dispute:
    unit: Unit
    groups: list[Group]


def word_pattern(text: str) -> re.Pattern[str]:
    left = r"(?<!\w)" if text[:1].isalnum() else ""
    right = r"(?!\w)" if text[-1:].isalnum() else ""
    return re.compile(left + re.escape(text) + right)


def name_shaped(text: str, index: NameIndex) -> bool:
    words = [w for w in re.split(r"[\s_.\-]+", text) if w]
    if not words or any(any(c.isdigit() for c in w) for w in words):
        return False
    if all(w[:1].isupper() for w in words):
        return True
    tokens = TOKEN.findall(fold(text))
    return any(t in index.given_lexicon or t in index.surname_lexicon for t in tokens)


def parse_birth(text: str) -> str | None:
    m = BIRTH_WORDS.search(fold(text))
    if not m or m.group(2) not in MONTHS:
        return None
    try:
        return dt.date(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1))).isoformat()
    except ValueError:
        return None


def spelled_email(text: str) -> tuple[str, str] | None:
    """(normalized email, render kind) for `a@b.co`, `a arroba b punto co` or `a at b dot co`."""
    lowered = " ".join(text.lower().split())
    if EMAIL_PLAIN.fullmatch(lowered):
        return lowered, ""
    english = " at " in f" {lowered} " and "arroba" not in lowered
    value = re.sub(r"\s*\b(?:arroba|at)\b\s*", "@", lowered)
    value = re.sub(r"\s*\b(?:punto|dot)\b\s*", ".", value).replace(" ", "")
    if EMAIL_PLAIN.fullmatch(value):
        return value, "spelled_en" if english else "spelled"
    return None


def age_value(text: str) -> int | None:
    m = AGE_NUMBER.search(text)
    if not m:
        return None
    age = int(m.group(1))
    return age if 0 < age <= 110 else None


def valid_shape(c: Candidate) -> bool:
    text = c.text.strip()
    digits = sum(ch.isdigit() for ch in text)
    if c.label == "PERSON":
        return (
            any(ch.isalpha() for ch in text)
            and digits == 0
            and "@" not in text
            and len(text) <= 60
            and len(text.split()) <= 6
            and fold(text) not in NOT_PERSONS
            and not set(TOKEN.findall(fold(text))) <= TITLES
        )
    if c.label == "PERSONAL_ADDRESS":
        return digits > 0 and len(text) <= 120
    if c.label == "BIRTH_DATE":
        return parse_birth(text) is not None
    if c.label == "AGE":
        return age_value(text) is not None
    if c.label in ("PASSPORT", "ACCOUNT", "PLATE"):
        alnum = sum(ch.isalnum() for ch in text)
        return digits >= 2 and alnum >= 5 and len(text) <= 40 and not RECORD_CODE.search(text)
    if c.label == "EMAIL":
        return spelled_email(text) is not None
    if c.label == "QUASI_ID":
        return any(ch.isalpha() for ch in text) and len(text) <= 120
    return False


class Verifier:
    def __init__(
        self,
        index: NameIndex,
        kept: KeptMatcher,
        covered: dict[Key, list[tuple[int, int]]],
        models: list[str],
        sources: list[str],
    ) -> None:
        """`covered`: spans the rules already replace or keep, per surface. `sources` names
        each model in reports ("primary", "second", "omission")."""
        self.index = index
        self.kept = kept
        self.covered = covered
        self.models = models
        self.source_of = dict(zip(models, sources, strict=True))

    def classify(
        self, unit: Unit, by_model: dict[str, list[Candidate]], single: bool = False
    ) -> tuple[list[Span], list[Group]]:
        """Accepted spans and the groups in dispute. `single`: only one model ran and no
        adjudicator will (the live endpoint): identifiers with a valid shape are accepted."""
        groups: dict[tuple[str, str], Group] = {}
        keep_votes: dict[str, set[str]] = defaultdict(set)
        for model in self.models:
            for c in by_model.get(model, []):
                folded = " ".join(fold(c.text).split())
                if c.label in KEEP_LABELS:
                    keep_votes[folded].add(model)
                    continue
                if not self._admissible(c) or not self.spans(unit, c, ""):
                    continue
                group = groups.setdefault((folded, c.label), Group(c, [], []))
                if model not in group.models:
                    group.models.append(model)
                    group.sources.append(self.source_of[model])
        accepted: list[Span] = []
        disputed: list[Group] = []
        for (folded, _), group in groups.items():
            others_keep = keep_votes[folded] - set(group.models)
            c = group.candidate
            if len(group.models) >= 2:
                accepted += self.spans(unit, c, "both")
            elif others_keep:
                disputed.append(group)
            elif (c.label == "PERSON" and name_shaped(c.text, self.index)) or (
                single and c.label in IDENTIFIER_SHAPED
            ):
                accepted += self.spans(unit, c, group.sources[0])
            else:
                disputed.append(group)
        return accepted, disputed

    def _admissible(self, c: Candidate) -> bool:
        tokens = tuple(TOKEN.findall(fold(c.text)))
        if not tokens or tokens in self.kept.terms or tokens in self.index.kept_terms:
            return False
        return valid_shape(c)

    def locate(self, unit: Unit, c: Candidate) -> list[tuple[int, int, int]]:
        """(item, start, end) of every literal occurrence of the candidate's text in the unit."""
        text = c.text.strip()
        if len(text) < 2:
            return []
        pattern = word_pattern(text)
        hits = [
            (i, m.start(), m.end())
            for i, item in enumerate(unit.items)
            for m in pattern.finditer(item.text)
        ]
        if self._context_only(c, text):
            in_item = [h for h in hits if h[0] == c.item] or hits
            matched = [h for h in in_item if context_matches(unit.items[h[0]], h, c)]
            hits = matched or ([in_item[0]] if c.item >= 0 and in_item else [])
        return hits

    def _context_only(self, c: Candidate, text: str) -> bool:
        """A single-word name is only taken where the model saw it: the same word elsewhere
        in the unit may be a place or a common word ("el agente Robledo" / "en Robledo")."""
        return c.label == "PERSON" and len(text.split()) == 1

    def _covered(self, record_id: str, field: str, start: int, end: int) -> bool:
        return any(start < ce and cs < end for cs, ce in self.covered.get((record_id, field), []))

    def spans(self, unit: Unit, c: Candidate, source: str) -> list[Span]:
        """The candidate's occurrences on every surface it lives on, minus rule spans."""
        found = []
        for i, start, end in self.locate(unit, c):
            item = unit.items[i]
            for t in item.targets:
                if self._covered(t.record_id, t.field, t.offset + start, t.offset + end):
                    continue
                found.append(
                    Span(
                        t.record_id,
                        t.field,
                        t.offset + start,
                        t.offset + end,
                        item.text[start:end],
                        c.label,
                        c.gender,
                        c.role,
                        source,
                    )
                )
        return found


def context_matches(item: Item, hit: tuple[int, int, int], c: Candidate) -> bool:
    _, start, end = hit
    left = " ".join(c.left.split())[-8:]
    right = " ".join(c.right.split())[:8]
    before = " ".join(item.text[:start].split())
    after = " ".join(item.text[end:].split())
    return (not left or before.endswith(left)) and (not right or after.startswith(right))


async def adjudicate(
    client: LLMClient, model: str, verifier: Verifier, disputes: list[Dispute]
) -> list[Span]:
    """One strong-model call per unit in dispute; `personal=true` verdicts become spans."""

    async def one(dispute: Dispute) -> list[Span]:
        listed = [
            {
                "id": i,
                "text": g.candidate.text,
                "label": g.candidate.label,
                "item": g.candidate.item,
            }
            for i, g in enumerate(dispute.groups)
        ]
        user = (
            render_unit(dispute.unit)
            + "\nMenciones en duda:\n"
            + json.dumps(listed, ensure_ascii=False)
        )
        data = await client.complete(model, ADJUDICATE_SYSTEM, user, VERDICT_SCHEMA, "verdicts")
        accepted = []
        for verdict in (data or {}).get("verdicts") or []:
            i = verdict.get("id") if isinstance(verdict, dict) else None
            if isinstance(i, int) and 0 <= i < len(dispute.groups) and verdict.get("personal"):
                accepted += verifier.spans(dispute.unit, dispute.groups[i].candidate, "strong")
        return accepted

    results = await asyncio.gather(*(one(d) for d in disputes))
    return [s for spans in results for s in spans]


def by_surface(spans: list[Span]) -> dict[Key, list[Span]]:
    """Non-overlapping spans per surface: the first of identical spans, then the longest."""
    grouped: dict[Key, dict[tuple[int, int], Span]] = defaultdict(dict)
    for s in spans:
        grouped[s.key].setdefault((s.start, s.end), s)
    result = {}
    for key, unique in grouped.items():
        chosen: list[Span] = []
        for s in sorted(unique.values(), key=lambda s: (s.start - s.end, s.start)):
            if not any(s.start < c.end and c.start < s.end for c in chosen):
                chosen.append(s)
        result[key] = sorted(chosen, key=lambda s: s.start)
    return result
