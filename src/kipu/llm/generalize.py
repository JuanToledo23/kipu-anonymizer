"""Generalization the mapping tables cannot do: unseen occupations and quasi-identifying
phrases in prose. One cached call per distinct string; the tables stay the first choice."""

import asyncio
import re
from typing import Any

from kipu.directory.names import fold
from kipu.llm.client import LLMClient
from kipu.llm.extract import SECURITY
from kipu.llm.identity import phrase_key
from kipu.replace.generalize import BARRIOS, FALLBACK_SECTOR, SECTORS, SECTORS_FOLDED

SECTOR_VALUES = sorted({*SECTORS.values(), FALLBACK_SECTOR})
SECTOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sector"],
    "properties": {"sector": {"type": "string", "enum": SECTOR_VALUES}},
}
PHRASE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["generalized"],
    "properties": {"generalized": {"type": "string"}},
}
SECTOR_SYSTEM = f"""Clasifica una ocupación en el sector más cercano de la lista permitida.

{SECURITY}"""
PHRASE_SYSTEM = f"""Generaliza una frase de un texto en español de Colombia para que deje de \
identificar a una persona, cambiando solo lo necesario y manteniendo la gramática: ocupación → \
sector ("odontóloga" → "profesional de la salud"), barrio → ciudad o "un barrio de <ciudad>", \
año de vinculación o de un hecho personal → "hace unos años". Devuelve la MISMA frase con solo \
esas partes reemplazadas: no agregues palabras, no la reescribas ni la expliques. Si no hay nada \
que generalizar, devuélvela igual.

{SECURITY}"""
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
WORD = re.compile(r"[a-z0-9]+")
GENERAL_WORDS = "hace unos anos un una barrio de del en la el ciudad colombia"
VOCABULARY = frozenset(WORD.findall(fold(" ".join([*SECTOR_VALUES, *BARRIOS, GENERAL_WORDS]))))


async def classify_occupations(
    client: LLMClient, model: str, occupations: list[str]
) -> dict[str, str]:
    """{folded occupation: sector} for occupations outside the mapping table."""
    unseen = sorted({fold(o).strip() for o in occupations if o.strip()} - set(SECTORS_FOLDED))

    async def one(occupation: str) -> tuple[str, str | None]:
        user = f"<datos>{occupation}</datos>"
        data = await client.complete(model, SECTOR_SYSTEM, user, SECTOR_SCHEMA, "sector")
        sector = (data or {}).get("sector")
        return occupation, sector if sector in SECTOR_VALUES else None

    results = await asyncio.gather(*(one(o) for o in unseen))
    return {o: s for o, s in results if s}


async def generalize_phrases(client: LLMClient, model: str, phrases: list[str]) -> dict[str, str]:
    """{phrase key: generalized phrase}: the mapping table for an occupation alone, else the
    model's answer if it passes `acceptable`."""
    distinct = {phrase_key(p): p for p in phrases}
    found = {k: s for k, p in distinct.items() if (s := table_phrase(p))}
    distinct = {k: p for k, p in distinct.items() if k not in found}

    async def one(key: str, phrase: str) -> tuple[str, str | None]:
        user = f"<datos>{phrase}</datos>"
        data = await client.complete(model, PHRASE_SYSTEM, user, PHRASE_SCHEMA, "generalized")
        value = same_leading_case(phrase, str((data or {}).get("generalized") or "").strip())
        return key, value if acceptable(phrase, value) else None

    results = await asyncio.gather(*(one(k, p) for k, p in sorted(distinct.items())))
    return found | {k: v for k, v in results if v}


def table_sector(occupation: str) -> str | None:
    """Sector of an occupation by the table: exact, or the head of a single entry
    ("diseñadora" → "diseñadora gráfica")."""
    key = phrase_key(occupation)
    if key in SECTORS_FOLDED:
        return SECTORS_FOLDED[key]
    heads = {SECTORS_FOLDED[k] for k in SECTORS_FOLDED if k.split()[0] == key}
    return heads.pop() if len(heads) == 1 else None


def table_phrase(phrase: str) -> str | None:
    """The phrase with its occupation swapped for the table's sector ("soy diseñadora" →
    "soy profesional de las industrias creativas"), when nothing else identifying remains."""
    words = list(WORD.finditer(fold(phrase)))
    runs = [
        (words[i].start(), words[j].end())
        for i in range(len(words))
        for j in range(i, min(i + 3, len(words)))
    ]
    for start, end in sorted(runs, key=lambda r: r[0] - r[1]):
        sector = table_sector(fold(phrase[start:end]))
        if sector is None:
            continue
        result = same_leading_case(phrase, phrase[:start] + sector + phrase[end:])
        _, _, rest = result.strip().partition(" ")
        if YEAR.search(result) or re.search(r"\b[A-ZÁÉÍÓÚÑ]\w{2,}", rest):
            return None
        return result
    return None


def same_leading_case(original: str, value: str) -> str:
    if not value or not original:
        return value
    first = value[0].upper() if original[0].isupper() else value[0].lower()
    return first + value[1:]


def acceptable(original: str, generalized: str) -> bool:
    """A replacement, not a rewrite: built only from the original's words and generalization
    vocabulary, dropping at least one original content word, without the original's proper
    nouns or years."""
    if not generalized or "\n" in generalized:
        return False
    words = set(WORD.findall(fold(generalized)))
    original_words = set(WORD.findall(fold(original)))
    if not words <= original_words | VOCABULARY:
        return False
    if not {w for w in original_words - words if len(w) >= 4 and not w.isdigit()} | (
        set(YEAR.findall(original)) - words
    ):
        return False
    _, _, rest = original.strip().partition(" ")  # the first word may start the sentence
    proper = {fold(w) for w in re.findall(r"\b[A-ZÁÉÍÓÚÑ]\w{2,}", rest)}
    years = set(YEAR.findall(original))
    return not (proper & words) and not any(y in generalized for y in years)
