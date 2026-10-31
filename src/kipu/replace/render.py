"""Positional replacement that keeps the form of the original (case, accents, separators)."""

import re
from collections import defaultdict

from kipu.detect.mention import Mention
from kipu.directory.directory import Directory, Person
from kipu.directory.names import NameParts, fold, split_name, strip_accents
from kipu.pseudo.surrogates import SurrogateFactory, SurrogateName
from kipu.replace.generalize import sector_for

GENERALIZED_YEAR = "hace unos años"
WORD_SPLIT = re.compile(r"[\s_.\-]+")


def case_style(span: str) -> str:
    letters = [c for c in span if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return "upper"
    if letters and all(c.islower() for c in letters):
        return "lower"
    return "title"


def apply_style(words: list[str], style: str, strip: bool) -> list[str]:
    if strip:
        words = [strip_accents(w) for w in words]
    if style == "upper":
        return [w.upper() for w in words]
    if style == "lower":
        return [w.lower() for w in words]
    return words


def fit_digits(span: str, digits: str) -> str:
    """Write `digits` into the digit slots of `span`, keeping separators and any extra prefix."""
    slots = sum(c.isdigit() for c in span)
    pad = slots - len(digits)
    out, i = [], 0
    for c in span:
        if c.isdigit():
            out.append(c if i < pad else digits[i - pad])
            i += 1
        else:
            out.append(c)
    return "".join(out)


def format_date(iso: str, kind: str) -> str:
    year, month, day = iso.split("-")
    if kind == "dmy":
        return f"{day}/{month}/{year}"
    if kind == "mdy":
        return f"{month}/{day}/{year}"
    return iso


def spell_email(email: str, english: bool) -> str:
    dot, at = (" dot ", " at ") if english else (" punto ", " arroba ")
    local, _, domain = email.partition("@")
    return local.replace(".", dot) + at + domain.replace(".", dot)


class Renderer:
    """Renders surrogates for mentions and remembers which pseudonym values were written."""

    def __init__(self, factory: SurrogateFactory, directory: Directory) -> None:
        self.factory = factory
        self.directory = directory
        self.used: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))

    def _note(self, person_id: str, field: str, value: str) -> str:
        if value not in self.used[person_id][field]:
            self.used[person_id][field].append(value)
        return value

    def render(self, m: Mention) -> str | None:
        person = self.directory.persons.get(m.person_id or "")
        if person is None or m.label == "KEPT":
            return None
        handler = getattr(self, f"_render_{m.label.lower()}", None)
        return handler(m, person) if handler else None

    def name_text(self, person: Person, span: str, original: NameParts | None = None) -> str:
        sur = self.factory.name(person)
        original = original or person.name
        words = WORD_SPLIT.split(span.strip())
        separator = _separator(span)
        given_words = _count_given(words, original)
        surname_words = len(words) - given_words
        given = list(sur.given if given_words >= len(original.given) else sur.given[:given_words])
        if separator == ".":
            given = given[:1]
        surnames = list(sur.surnames[: max(0, surname_words)])
        style = case_style(span)
        strip = span.isascii() and (
            separator != " " or style != "title" or _covers_accented(words, original)
        )
        rendered = separator.join(apply_style(given + surnames, style, strip))
        if separator == " " and given and surnames:
            field = "name" if len(surnames) == 1 else "name_full"
            self._note(person.person_id, field, " ".join(given + surnames))
        elif separator == " " and given:
            self._note(person.person_id, "name_partial", " ".join(given))
        elif separator == ".":
            self._note(person.person_id, "handle", rendered)
        return rendered

    def short_name(self, person: Person) -> str:
        sur: SurrogateName = self.factory.name(person)
        return self._note(person.person_id, "name", sur.short)

    def _render_name(self, m: Mention, person: Person) -> str:
        if m.kind == "surname":
            return apply_style([self.factory.name(person).surnames[0]], case_style(m.text), False)[
                0
            ]
        original = split_name(m.text) if m.kind == "external" else None
        if m.kind == "first":
            return self._first_name(m.text, person)
        return self.name_text(person, m.text, original)

    def _first_name(self, span: str, person: Person) -> str:
        sur = self.factory.name(person)
        n = len(WORD_SPLIT.split(span.strip()))
        given = list(sur.given if n >= len(person.name.given) else sur.given[:n])
        style = case_style(span)
        strip = span.isascii() and not person.name.first.isascii()
        rendered = " ".join(apply_style(given, style, strip))
        self._note(person.person_id, "name_partial", " ".join(given))
        return rendered

    def _render_slack(self, m: Mention, person: Person) -> str:
        return "@" + self.short_name(person)

    def _render_email(self, m: Mention, person: Person) -> str:
        email = self.factory.email(person, m.value)
        if m.kind.startswith("spelled"):
            return spell_email(email, m.kind == "spelled_en")
        self._note(person.person_id, "email", email)
        return email.upper() if case_style(m.text) == "upper" else email

    def _render_cedula(self, m: Mention, person: Person) -> str:
        value = self.factory.cedula(person, m.value)
        self._note(person.person_id, "cedula", value)
        return fit_digits(m.text, value)

    def _render_phone(self, m: Mention, person: Person) -> str:
        value = self.factory.phone(person, m.value[-10:])
        self._note(person.person_id, "phone", value)
        return fit_digits(m.text, value)

    def _render_birth(self, m: Mention, person: Person) -> str:
        rendered = format_date(self.factory.birth(person, m.value), m.kind)
        return self._note(person.person_id, "birth_iso", rendered)

    def _render_address(self, m: Mention, person: Person) -> str:
        return self._note(person.person_id, "address", self.factory.address(person, m.value))

    def _render_plate(self, m: Mention, person: Person) -> str:
        return self._note(person.person_id, "plate", self.factory.plate(person, m.value))

    def _render_passport(self, m: Mention, person: Person) -> str:
        return self._note(person.person_id, "passport", self.factory.passport(person, m.value))

    def _render_quasi_barrio(self, m: Mention, person: Person) -> str | None:
        return person.attrs.get("ciudad") or None

    def _render_quasi_occupation(self, m: Mention, person: Person) -> str | None:
        return sector_for(person.attrs.get("ocupacion", "")) or None

    def _render_quasi_year(self, m: Mention, person: Person) -> str:
        return GENERALIZED_YEAR


def _separator(span: str) -> str:
    m = re.search(r"[ \t_.\-]+", span.strip())
    if not m:
        return " "
    sep = m.group(0)
    return sep if sep in ("_", ".", "-") else " "


def _covers_accented(words: list[str], original: NameParts) -> bool:
    """True if the span wrote, without accents, a name token that has them."""
    folded = {fold(w) for w in words}
    return any(fold(t) in folded and not t.isascii() for t in original.given + original.surnames)


def _count_given(words: list[str], original: NameParts) -> int:
    given = [fold(g) for g in original.given]
    count = 0
    for word, expected in zip(words, given, strict=False):
        if fold(word) != expected:
            break
        count += 1
    return max(count, 1) if len(words) > 1 else count


def apply_mentions(text: str, mentions: list[Mention], renderer: Renderer) -> str:
    """Replace right to left so earlier offsets stay valid."""
    out = text
    for m in sorted(mentions, key=lambda m: m.start, reverse=True):
        replacement = renderer.render(m)
        if replacement is not None and replacement != m.text:
            out = out[: m.start] + replacement + out[m.end :]
    return out
