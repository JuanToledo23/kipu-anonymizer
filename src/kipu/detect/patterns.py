"""Regex detectors for identifiers. They return Mentions without an owner."""

import re
from collections.abc import Iterator

from kipu.detect.mention import Mention
from kipu.directory.names import fold

EMAIL = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
)
TLDS = r"(?:com|co|net|org|edu|gov|io|es|us)"
SPELLED_EMAIL = re.compile(
    rf"(?<![\w.])([a-z0-9_]+(?:\s+(?:punto|dot)\s+[a-z0-9_]+)*)\s+(?:arroba|at)\s+"
    rf"([a-z0-9-]+(?:\s+(?:punto|dot)\s+[a-z0-9-]+)*\s+(?:punto|dot)\s+{TLDS})(?![\w])",
    re.I,
)
DIGIT_RUN = re.compile(r"(?<![\d])(\+?\d+(?:[ .\-]\d+)*)(?![\d])")
PLATE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{3}\d{3}|[A-Z]{3}\d{2}[A-Z])(?![A-Za-z0-9])")
PASSPORT_CONTEXT = re.compile(
    r"(?:pasaporte|passport)(?:\s+(?:number|no\.?|n[úu]mero))?(?:\s+(?:is|es|:))?\s*", re.I
)
PASSPORT_VALUE = re.compile(r"[A-Z]{1,3}\d{6,9}")
ISO_DATE = re.compile(
    r"(?<![\d-])(19[3-9]\d|200[0-8])-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])(?![\d-])"
)
SLASH_DATE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(19[3-9]\d|200[0-8])(?![\d/])")
SLACK = re.compile(r"<@([A-Z0-9]{6,})>")
ADDRESS = re.compile(
    r"(?<![\w])(?:Calle|Cl|Cll|Carrera|Cra|Kr|Kra|Avenida|Av|Diagonal|Dg|Transversal|Tv|Tr)\.?"
    r"\s+\d+[A-Z]?(?:\s+Bis)?(?:\s+Sur)?\s*(?:#|No\.?|N°)\s*\d+[A-Z]?\s*-\s*\d+"
    r"(?:\s+(?:Sur|Este))?"
    r"(?:\s*,?\s+(?:torre|apto|apartamento|of|oficina|casa|int|interior|local)\.?\s+\d+[A-Z]?)*",
    re.I,
)
CEDULA_CONTEXT = re.compile(
    r"(?:\bc\.?\s?c\.?|\bcc|c[ée]dula|documento|\bdoc\.?|\bid\b|identificaci[oó]n|\bc\.?e\.?|\bnit)"
    r"[\s:#.]*(?:(?:no|n[uú]mero|es|is)[\s:.]*)?$",
    re.I,
)
CEDULA_REQUEST = re.compile(r"c[eé]dula|documento|\bcc\b|n[uú]mero de identificaci", re.I)
MONEY_BEFORE = re.compile(r"(?:\$|cop|usd|pesos?)\s*$", re.I)
MONEY_AFTER = re.compile(r"^\s*(?:pesos|mil\b|millones|cop\b|usd\b|m\b|%)", re.I)


def find_emails(text: str) -> Iterator[Mention]:
    for m in EMAIL.finditer(text):
        yield Mention(
            m.start(), m.end(), m.group(0), "EMAIL", kind="plain", value=m.group(0).lower()
        )
    for m in SPELLED_EMAIL.finditer(text):
        local = re.sub(r"\s+(?:punto|dot)\s+", ".", m.group(1), flags=re.I)
        domain = re.sub(r"\s+(?:punto|dot)\s+", ".", m.group(2), flags=re.I)
        english = bool(re.search(r"\b(?:at|dot)\b", m.group(0), re.I))
        yield Mention(
            m.start(),
            m.end(),
            m.group(0),
            "EMAIL",
            kind="spelled_en" if english else "spelled",
            value=f"{local}@{domain}".lower(),
        )


def digits_of(s: str) -> str:
    return re.sub(r"\D", "", s)


def find_numbers(text: str, cedula_requested: bool = False) -> Iterator[Mention]:
    """Phones by shape; cédulas only with a context word or as a reply to a cédula request."""
    for runs in find_digit_runs(text):
        for start, end, raw in runs:
            mention = _classify_number(text, start, end, raw, cedula_requested)
            if mention:
                yield mention
                break


def _classify_number(
    text: str, start: int, end: int, raw: str, cedula_requested: bool
) -> Mention | None:
    digits = digits_of(raw)
    if _is_phone(raw, digits):
        return Mention(start, end, raw, "PHONE", value=digits[-10:])
    if not 6 <= len(digits) <= 10 or _looks_like_money(text, start, end) or raw.count("-") > 1:
        return None
    before = text[max(0, start - 30) : start]
    dotted = re.fullmatch(r"\d{1,3}(?:\.\d{3}){2,3}", raw) is not None
    short_reply = cedula_requested and len(text) < 80 and (dotted or raw.isdigit())
    if CEDULA_CONTEXT.search(before) or short_reply:
        return Mention(start, end, raw, "CEDULA", value=digits)
    return None


def find_digit_runs(text: str) -> Iterator[list[tuple[int, int, str]]]:
    """Each digit run with separators, followed by its space-separated parts as fallbacks."""
    for m in DIGIT_RUN.finditer(text):
        whole = (m.start(1), m.end(1), m.group(1))
        parts = [
            (m.start(1) + p.start(), m.start(1) + p.end(), p.group(0))
            for p in re.finditer(r"[^ ]+", m.group(1))
        ]
        yield [whole, *parts] if len(parts) > 1 else [whole]


PHONE_SHAPE = re.compile(r"\d{3}[ .\-]?\d{3}[ .\-]?(?:\d{2}[ .\-]?\d{2}|\d{4})")


def _is_phone(raw: str, digits: str) -> bool:
    if len(digits) == 10 and digits.startswith("3"):
        return PHONE_SHAPE.fullmatch(raw) is not None
    return len(digits) == 12 and digits.startswith("573")


def _looks_like_money(text: str, start: int, end: int) -> bool:
    return bool(MONEY_BEFORE.search(text[max(0, start - 6) : start])) or bool(
        MONEY_AFTER.search(text[end : end + 10])
    )


def find_plates(text: str) -> Iterator[Mention]:
    for m in PLATE.finditer(text):
        yield Mention(m.start(1), m.end(1), m.group(1), "PLATE", value=m.group(1))


def find_passports(text: str) -> Iterator[Mention]:
    for ctx in PASSPORT_CONTEXT.finditer(text):
        m = PASSPORT_VALUE.match(text, ctx.end())
        if m:
            yield Mention(m.start(), m.end(), m.group(0), "PASSPORT", value=m.group(0))


def find_birth_dates(text: str) -> Iterator[Mention]:
    """Dates with a year in 1930–2008: in this corpus those are birth dates, not events."""
    for m in ISO_DATE.finditer(text):
        yield Mention(m.start(), m.end(), m.group(0), "BIRTH", kind="iso", value=m.group(0))
    for m in SLASH_DATE.finditer(text):
        a, b, year = int(m.group(1)), int(m.group(2)), m.group(3)
        day, month, kind = (b, a, "mdy") if b > 12 >= a else (a, b, "dmy")
        if 1 <= month <= 12 and 1 <= day <= 31:
            iso = f"{year}-{month:02d}-{day:02d}"
            yield Mention(m.start(), m.end(), m.group(0), "BIRTH", kind=kind, value=iso)


def find_addresses(text: str) -> Iterator[Mention]:
    for m in ADDRESS.finditer(text):
        yield Mention(
            m.start(), m.end(), m.group(0), "ADDRESS", value=" ".join(fold(m.group(0)).split())
        )


def find_slack_mentions(text: str) -> Iterator[Mention]:
    for m in SLACK.finditer(text):
        yield Mention(m.start(), m.end(), m.group(0), "SLACK", value=m.group(1))


def asks_for_cedula(text: str) -> bool:
    return bool(CEDULA_REQUEST.search(text))


def find_identifiers(text: str, cedula_requested: bool = False) -> list[Mention]:
    found: list[Mention] = []
    found.extend(find_emails(text))
    found.extend(find_numbers(text, cedula_requested))
    found.extend(find_plates(text))
    found.extend(find_passports(text))
    found.extend(find_birth_dates(text))
    found.extend(find_addresses(text))
    found.extend(find_slack_mentions(text))
    return found
