"""Who a model-found person is, and the Mentions the detector receives for accepted spans.

Called by the detector for each surface after the rules have resolved it (`Detector.extra`),
so a span the rules already cover is never touched and rule output stays the same.
"""

import json
import re
from collections.abc import Callable
from typing import Any

from kipu.detect.engine import Group, Surface
from kipu.detect.mention import Mention
from kipu.detect.names import TOKEN, NameIndex
from kipu.directory.directory import Directory, Person
from kipu.directory.names import fold
from kipu.llm.extract import SECURITY
from kipu.llm.verify import AGE_NUMBER, Key, Span, parse_birth, spelled_email

GENDER = {"f": "F", "m": "M"}
STRONG_KINDS = ("full", "short")
INSUFFICIENT = "insufficient"
EVIDENCE_TYPES = ("cliente_id", "kp_id", "cedula", "email", "phone", "handle", "radicado")

HOMONYM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["person_id", "evidence"],
    "properties": {"person_id": {"type": "string"}, "evidence": {"type": "string"}},
}
HOMONYM_SYSTEM = f"""Decides a cuál de varias personas homónimas se refiere un nombre en un \
texto de soporte de Kipu Pay.

{SECURITY}

Recibes el texto, el nombre y los candidatos con la evidencia de cada uno que aparece en el \
texto (identificadores, participación en el hilo). Responde person_id con el id de un \
candidato SOLO si el texto contiene evidencia que lo distingue, y copia esa evidencia literal \
en evidence. Si no alcanza, responde person_id="insufficient" y evidence=""."""

# (name text, candidate ids, group, evidence per candidate) → chosen id or None.
HomonymJudge = Callable[[str, list[str], str, dict[str, list[str]]], str | None]


def generalize_age(text: str) -> str | None:
    m = AGE_NUMBER.search(text)
    if not m:
        return None
    low = int(m.group(1)) // 10 * 10
    return f"{text[: m.start()]}entre {low} y {low + 9}{text[m.end() :]}"


def compact(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", value).upper()


class LLMMentions:
    """The `Detector.extra` hook: accepted spans → Mentions with source="llm"."""

    def __init__(
        self,
        spans: dict[Key, list[Span]],
        directory: Directory,
        index: NameIndex,
        surrogates: set[str],
        quasi: dict[str, str],
        judge: HomonymJudge | None = None,
    ) -> None:
        self.spans = spans
        self.directory = directory
        self.index = index
        self.surrogates = surrogates
        self.quasi = quasi
        self.judge = judge
        self.created: set[str] = set()
        self.sources: dict[tuple[str, str, int], str] = {}

    def __call__(
        self, surface: Surface, group: Group, context: list[str], rules: list[Mention]
    ) -> list[Mention]:
        found = []
        for span in self.spans.get((surface.record_id, surface.field), []):
            if surface.text[span.start : span.end] != span.text:
                continue
            if any(span.start < r.end and r.start < span.end for r in rules):
                continue
            mention = self._mention(span, group, context)
            if mention is not None:
                found.append(mention)
                self.sources[(surface.record_id, surface.field, span.start)] = span.source
        return found

    def _mention(self, span: Span, group: Group, context: list[str]) -> Mention | None:
        s, e, text = span.start, span.end, span.text
        if span.label == "PERSON":
            owner = self._person(span, group, context)
            if owner is None:
                return None
            person_id, kind = owner
            return Mention(s, e, text, "NAME", person_id, source="llm", kind=kind)
        if span.label == "PERSONAL_ADDRESS":
            value = " ".join(fold(text).split())
            return Mention(s, e, text, "ADDRESS", source="llm", value=value)
        if span.label == "BIRTH_DATE":
            iso = parse_birth(text)
            return (
                Mention(s, e, text, "BIRTH", source="llm", kind="words", value=iso) if iso else None
            )
        if span.label in ("PASSPORT", "ACCOUNT", "PLATE"):
            return Mention(s, e, text, span.label, source="llm", value=compact(text))
        if span.label == "EMAIL":
            parsed = spelled_email(text)
            if parsed is None:
                return None
            value, kind = parsed
            return Mention(s, e, text, "EMAIL", source="llm", kind=kind, value=value)
        if span.label == "AGE":
            value = generalize_age(text)
            return Mention(s, e, text, "QUASI_AGE", source="llm", value=value) if value else None
        if span.label == "QUASI_ID":
            value = self.quasi.get(phrase_key(text))
            return Mention(s, e, text, "QUASI_LLM", source="llm", value=value) if value else None
        return None

    def _person(self, span: Span, group: Group, context: list[str]) -> tuple[str, str] | None:
        tokens = tuple(TOKEN.findall(fold(span.text)))
        if not tokens or " ".join(tokens) in self.surrogates:
            return None
        if span.person_id:
            return span.person_id, "llm_given" if len(tokens) == 1 else "llm_full"
        gender = GENDER.get(span.gender, "")
        if len(tokens) == 1:
            return self._single(span.text, tokens[0], gender, group, context)
        hits = self.index.entries.get(tokens, {})
        strong = sorted(p for p, kind in hits.items() if kind in STRONG_KINDS)
        if len(strong) == 1:
            return strong[0], "llm_full"
        if strong:
            chosen = next((p for p in context if p in strong), None) or self._judge(
                span.text, strong, group
            )
            key = "" if chosen else f"homonym:{group.scope}"
            return chosen or self._external(span.text, key, gender), "llm_full"
        return self._external(span.text, "", gender), "llm_full"

    def _single(
        self, text: str, token: str, gender: str, group: Group, context: list[str]
    ) -> tuple[str, str]:
        """A given name, nickname or surname alone: a participant, else the only employee with
        that given name, else a new third party scoped to this thread or record."""
        scope = f"scope:{group.scope}"
        if token in self.index.surname_lexicon and token not in self.index.given_lexicon:
            bearers = {p for p in self.index.surnames.get(token, set())}
            chosen = next((p for p in context if p in bearers), None)
            return chosen or self._external(text, scope, gender), "surname"
        named = self.index.entries.get((token,), {})
        chosen = next((p for p in context if p in named), None)
        if chosen is None:
            employees = [p for p in named if self._kind(p) == "employee"]
            chosen = employees[0] if len(employees) == 1 else None
        return chosen or self._external(text, scope, gender), "llm_given"

    def _kind(self, person_id: str) -> str:
        person = self.directory.persons.get(person_id)
        return person.kind if person else ""

    def _external(self, name: str, key: str, gender: str) -> str:
        before = len(self.directory.created)
        person = self.directory.external_person(name, key, gender)
        if len(self.directory.created) > before:
            self.created.add(person.person_id)
        return person.person_id

    def _judge(self, name: str, candidates: list[str], group: Group) -> str | None:
        """The strong model picks a homonym only on evidence present in the text, which the
        code checks; no evidence at all means no call."""
        if self.judge is None:
            return None
        text = "\n".join(s.text for s in group.surfaces)
        evidence = {p: present_evidence(self.directory.persons[p], text) for p in candidates}
        if not any(evidence.values()):
            return None
        return self.judge(name, candidates, text, evidence)


def present_evidence(person: Person, text: str) -> list[str]:
    folded = fold(text)
    found = []
    for id_type in EVIDENCE_TYPES:
        for value in sorted(person.identifiers.get(id_type, set())):
            if len(value) >= 4 and fold(value) in folded:
                found.append(value)
    return found


def homonym_prompt(name: str, candidates: list[str], text: str, evidence: dict[str, list[str]]):
    listed = [{"person_id": p, "evidencia_en_el_texto": evidence[p]} for p in candidates]
    return f"<datos>\n{text}\n</datos>\nNombre: {name}\nCandidatos:\n" + json.dumps(
        listed, ensure_ascii=False
    )


def checked_verdict(
    data: dict[str, Any] | None, candidates: list[str], text: str, evidence: dict[str, list[str]]
) -> str | None:
    """The answer counts only if it names a candidate and cites evidence that is in the text
    and belongs to that candidate."""
    if not data:
        return None
    chosen, cited = data.get("person_id"), str(data.get("evidence") or "")
    if chosen == INSUFFICIENT or chosen not in candidates or not cited:
        return None
    folded_text, folded_cited = fold(text), fold(cited)
    if folded_cited not in folded_text:
        return None
    return chosen if any(fold(v) in folded_cited for v in evidence[chosen]) else None


def phrase_key(text: str) -> str:
    return " ".join(fold(text).split())
