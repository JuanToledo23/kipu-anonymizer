"""Quasi-identifiers in free text, generalized wherever they occur, whoever they describe:
occupations → sector, employee cargos → their family, barrios → city, the year someone joined
→ "hace unos años", an age → its decade. Each mention carries its replacement in `value`.

Linking a phrase to its person is not needed: barrio + ocupación + año identify someone only
together, so every component is generalized on sight (the person only picks the city).
"""

import re
from collections import Counter, defaultdict
from collections.abc import Iterable

from kipu.detect.mention import Mention
from kipu.directory.directory import Person
from kipu.directory.names import fold
from kipu.replace.generalize import (
    BARRIOS,
    CARGO_FAMILIES,
    COMMON_SECTORS,
    GENERALIZED_YEAR,
    KNOWN_BARRIOS,
    SECTORS,
    age_phrase,
    inflections,
    plural_phrase,
    sector_for,
)

UPPER = "A-ZÁÉÍÓÚÑÜ"
# Feminine forms that are everyday adjectives ("incapacidad médica", "falla mecánica").
ADJECTIVE_FORMS = frozenset({"medica", "mecanica", "musica", "quimica", "tecnica", "grafica"})
# Part of a proper name ("Auditores Andinos") or a front-matter key ("auditora: …").
NAME_AFTER = re.compile(rf"[ \t]+[{UPPER}]")
KEY_AFTER = re.compile(r":")
# Kipu's own premises are not anyone's barrio ("las oficinas … en El Poblado").
OWN_PLACE = re.compile(r"\b(?:las oficinas|nuestras oficinas|la sede|nuestra sede|offices|kipu)\b")
# A barrio counts only right after a place cue ("vivo en Laureles", "del barrio Galán").
BARRIO_CUE = re.compile(
    r"(?:\b(?:en|de|del|por|al|a|hacia|desde|sector|barrio)\s+(?:el\s+|la\s+|los\s+|las\s+)?)$"
)
BARRIO_PREFIX = re.compile(r"(?:\b(?:(del)|el)\s+)?\bbarrio\s+$")
YEAR = re.compile(r"(?<![\w/.,$-])(20(?:1[5-9]|2[0-6]))(?![\w/-]|[.,]\d)")
SHORT_YEAR = re.compile(r"\b(?:desde|en)\s+(?:el\s+)?('?)(1[5-9]|2[0-6])(?![\w/.,-])")
# The article belongs to the year ("desde el año 2021"); "desde" or "Ingreso:" right before
# it is a joining cue by itself.
YEAR_ARTICLE = re.compile(r"\bel\s+(?:ano\s+)?$")
SINCE = re.compile(r"\b(?:desde|ingreso:?)\s+(?:el\s+(?:ano\s+)?)?$")
MONTH_AFTER = re.compile(
    r"\s+de\s+(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre"
    r"|noviembre|diciembre)\b"
)
JOINED = re.compile(
    r"\b(?:vincul\w+|me afilie|afiliad[oa]s?|abri (?:la|mi|una) cuenta|abrio (?:la|su|una) cuenta"
    r"|ingrese|entro a|me registre|soy cliente|es cliente|es clienta|cliente desde"
    r"|clienta desde|con (?:ustedes|nosotros) desde|trabaj\w+ (?:aqui|aca|en kipu) desde"
    r"|llev[oa] con (?:ustedes|nosotros))\b"
)
# Joining verbs whose folded form is another word ("entré" → "entre").
JOINED_ACCENTED = re.compile(r"\b(?:entré|entró)\b")
JOINED_WINDOW = 60
DATE_BEFORE = re.compile(
    r"(?:\d{1,2}|enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre"
    r"|noviembre|diciembre|q[1-4]|semestre|trimestre)\s*(?:de|del)?\s*$"
)
SENTENCE_BREAK = re.compile(r"[.!?\n](?:\s|$)")
PERSON_NOUNS = (
    r"senor|senora|cliente|clienta|hombre|mujer|persona|joven|titular|afectad[oa]|usuari[oa]"
    r"|hij[oa]|madre|padre|mama|papa|abuel[oa]|espos[oa]|herman[oa]|muchach[oa]|chic[oa]"
)
AGE = re.compile(
    r"\b(?:tengo|cumpli|cumplo|voy a cumplir)\s+(\d{2})\s+anos\b"
    r"|\b(\d{2})\s+anos\s+de\s+edad\b"
    rf"|\b(?:{PERSON_NOUNS}),?\s+de\s+(\d{{2}})\s+anos\b"
    r"|\bedad:?\s+(?:de\s+)?(\d{2})\b"
)
MIN_AGE = 15


class QuasiLexicon:
    """Occupations, cargos and barrios to generalize, built from the directory (every
    clientes.ocupacion/barrio and empleados.cargo) plus fixed lists of common ones."""

    def __init__(
        self,
        persons: Iterable[Person],
        sectors: dict[str, str] | None = None,
        kept: Iterable[str] = (),
    ) -> None:
        persons = list(persons)
        self.occupations: dict[str, tuple[str, bool]] = {}
        for form, sector in {**COMMON_SECTORS, **SECTORS}.items():
            for variant, is_plural in inflections(form, gender=False).items():
                self.occupations.setdefault(variant, (sector, is_plural))
        for person in persons:
            occupation = person.attrs.get("ocupacion", "")
            for variant, is_plural in inflections(occupation).items():
                self.occupations.setdefault(variant, (sector_for(occupation, sectors), is_plural))
        for form in ADJECTIVE_FORMS:
            self.occupations.pop(form, None)
        self.cargos = _cargos(persons)
        self.cities = {fold(c) for c in (*BARRIOS, *KNOWN_BARRIOS)}
        self.cities |= {fold(p.attrs["ciudad"]) for p in persons if p.attrs.get("ciudad")}
        self.barrios: dict[str, set[str]] = defaultdict(set)
        for city, names in [*BARRIOS.items(), *KNOWN_BARRIOS.items()]:
            for name in names:
                self.barrios[fold(name)].add(city)
        for person in persons:
            barrio, city = person.attrs.get("barrio", ""), person.attrs.get("ciudad", "")
            if barrio and city:
                self.barrios[fold(barrio)].add(city)
        for name in self.cities:
            self.barrios.pop(name, None)
        # Kept places ("Itagüí") stay unless they are the barrio of a person in the record.
        self.kept_barrios = {fold(k) for k in kept} & set(self.barrios)
        terms = sorted({*self.occupations, *self.cargos, *self.barrios}, key=lambda t: -len(t))
        alternation = "|".join(re.escape(t) for t in terms)
        self.pattern = re.compile(rf"(?<![a-z0-9])(?:{alternation})(?![a-z0-9])")

    def scan(self, text: str, context: list[Person]) -> list[Mention]:
        folded = fold(text)
        found = []
        for m in self.pattern.finditer(folded):
            key = m.group(0)
            if key in self.occupations and not _job_title(text, m.start(), m.end()):
                continue
            if key in self.cargos:
                found.append(self._cargo(text, m.start(), m.end()))
            elif key in self.occupations:
                found.append(self._occupation(text, m.start(), m.end()))
            else:
                mention = self._barrio(text, folded, m.start(), m.end(), context)
                if mention is not None:
                    found.append(mention)
        found += _years(text, folded) + _ages(text, folded)
        return found + _own_years(text, folded, context, found)

    def _occupation(self, text: str, start: int, end: int) -> Mention:
        sector, is_plural = self.occupations[fold(text[start:end])]
        value = plural_phrase(sector) if is_plural else sector
        span = text[start:end]
        return Mention(start, end, span, "QUASI_OCCUPATION", value=_lead_case(span, value))

    def _cargo(self, text: str, start: int, end: int) -> Mention:
        span = text[start:end]
        family = self.cargos[fold(span)] or span.split()[0]
        return Mention(start, end, span, "QUASI_OCCUPATION", value=_lead_case(span, family))

    def _barrio(
        self, text: str, folded: str, start: int, end: int, context: list[Person]
    ) -> Mention | None:
        if not text[start].isupper():
            return None
        barrio = fold(text[start:end])
        # The barrio of a person in the record needs no cue ("Compra D1 Restrepo" in their
        # transaction); any other only after a place cue, and not inside a longer name.
        own = any(fold(p.attrs.get("barrio", "")) == barrio for p in context)
        if not own and barrio in self.kept_barrios:
            return None
        if not own and re.match(rf"\s+[{UPPER}]", text[end:]):
            return None
        if OWN_PLACE.search(folded[max(0, start - 80) : start]):
            return None
        # "…, Suba, Bogotá" (an address tail): the barrio and its city become the city.
        tail = re.match(r",\s*([a-z]+(?: [a-z]+)?)\b", folded[end:])
        if tail and tail.group(1) in {fold(c) for c in self.barrios[barrio]}:
            city = text[end + tail.start(1) : end + tail.end(1)]
            return Mention(
                start, end + tail.end(), text[start : end + tail.end()], "QUASI_BARRIO", value=city
            )
        before = folded[max(0, start - 40) : start]
        prefix = BARRIO_PREFIX.search(before)
        if prefix is None and not own and not BARRIO_CUE.search(before):
            return None
        city = self._city(barrio, folded, context)
        if prefix is not None:
            start -= len(before) - prefix.start()
            city = f"de {city}" if prefix.group(1) else city
        return Mention(start, end, text[start:end], "QUASI_BARRIO", value=city)

    def _city(self, barrio: str, folded: str, context: list[Person]) -> str:
        """The linked person's city, else the barrio's own city (one named in the text when
        the barrio name exists in several)."""
        cities = self.barrios[barrio]
        for person in context:
            if fold(person.attrs.get("barrio", "")) == barrio and person.attrs.get("ciudad"):
                return person.attrs["ciudad"]
        for person in context:
            if person.attrs.get("ciudad") in cities:
                return person.attrs["ciudad"]
        named = [c for c in sorted(cities) if re.search(rf"\b{re.escape(fold(c))}\b", folded)]
        return (named or sorted(cities))[0]


def _cargos(persons: list[Person]) -> dict[str, str]:
    """{folded cargo form: family ("" keeps the head word)} for the cargos that, with the
    city and the year of joining, single out an employee."""
    triples = [
        (fold(p.attrs["cargo"]), fold(p.attrs.get("ciudad", "")), p.attrs.get("ingreso", "")[:4])
        for p in persons
        if p.attrs.get("cargo")
    ]
    counts = Counter(triples)
    cargos: dict[str, str] = {}
    for cargo, city, year in triples:
        if counts[(cargo, city, year)] > 1:
            continue
        family = CARGO_FAMILIES.get(cargo, "")
        if not family and len(cargo.split()) < 2:
            continue
        for variant, is_plural in inflections(cargo).items():
            cargos[variant] = plural_phrase(family) if family and is_plural else family
    return cargos


def _years(text: str, folded: str) -> list[Mention]:
    """Years of joining Kipu ("cliente desde 2021", "vinculada en 2023"); not dates or
    amounts."""
    found = []
    candidates = [(m.start(1), m.end(1)) for m in YEAR.finditer(folded)]
    candidates += [(m.start(1), m.end(2)) for m in SHORT_YEAR.finditer(folded)]
    for start, end in candidates:
        before = folded[max(0, start - 30) : start]
        if DATE_BEFORE.search(before) or MONTH_AFTER.match(folded, end):
            continue
        if not (SINCE.search(before) or _joined(text.lower(), start, end)):
            continue
        article = YEAR_ARTICLE.search(before)
        if article is not None:
            start -= len(article.group(0))
        found.append(Mention(start, end, text[start:end], "QUASI_YEAR", value=GENERALIZED_YEAR))
    return found


def _own_years(
    text: str, folded: str, context: list[Person], found: list[Mention]
) -> list[Mention]:
    """The year a person in the record joined, anywhere in the text, once another of their
    components is there too (an occupation or barrio just generalized)."""
    if not any(m.label in ("QUASI_OCCUPATION", "QUASI_BARRIO") for m in found):
        return []
    years = {(p.attrs.get("vinculacion") or p.attrs.get("ingreso") or "")[:4] for p in context}
    taken = [(m.start, m.end) for m in found]
    own = []
    for m in YEAR.finditer(folded):
        start, end = m.span(1)
        if m.group(1) not in years or any(s < end and start < e for s, e in taken):
            continue
        if DATE_BEFORE.search(folded[max(0, start - 30) : start]) or MONTH_AFTER.match(folded, end):
            continue
        own.append(Mention(start, end, text[start:end], "QUASI_YEAR", value=GENERALIZED_YEAR))
    return own


def _joined(lowered: str, start: int, end: int) -> bool:
    """A joining cue within the window around the year, in the same sentence."""
    left = SENTENCE_BREAK.split(lowered[max(0, start - JOINED_WINDOW) : start])[-1]
    right = SENTENCE_BREAK.split(lowered[end : end + JOINED_WINDOW])[0]
    return any(JOINED.search(fold(part)) or JOINED_ACCENTED.search(part) for part in (left, right))


def _ages(text: str, folded: str) -> list[Mention]:
    found = []
    for m in AGE.finditer(folded):
        group = next(g for g in range(1, 5) if m.group(g))
        age = int(m.group(group))
        phrase = age_phrase(age) if age >= MIN_AGE else None
        if phrase:
            start, end = m.start(group), m.end(group)
            found.append(Mention(start, end, text[start:end], "QUASI_AGE", value=phrase))
    return found


def _job_title(text: str, start: int, end: int) -> bool:
    """An occupation used as such, not inside a proper name or as a front-matter key."""
    line_start = text.rfind("\n", 0, start) + 1
    if KEY_AFTER.match(text, end) and not text[line_start:start].strip():
        return False
    return not NAME_AFTER.match(text, end)


def _lead_case(original: str, value: str) -> str:
    if original.isupper() and len(original) > 1:  # an acronym: "el CEO" → "el directivo"
        return value
    return value[:1].upper() + value[1:] if original[:1].isupper() else value
