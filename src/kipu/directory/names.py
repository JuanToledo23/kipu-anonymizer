"""Name helpers: offset-preserving folding, given/surname split and gender inference."""

import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from kipu.audit.texto import CONFUSABLES
from kipu.pseudo.names_co import FEMALE_GIVEN, MALE_GIVEN

EXTRA_FEMALE = (
    "Carmenza",
    "Juanita",
    "Julieth",
    "Katherine",
    "Kelly",
    "Lizeth",
    "Magnolia",
    "Nathalia",
    "Nohora",
    "Ofelia",
    "Pamela",
    "Paz",
    "Renata",
    "Yuliana",
    "Ivonne",
    "Celeste",
    "Adela",
    "Amalia",
)
EXTRA_MALE = (
    "Aníbal",
    "Alexander",
    "Álex",
    "Brayan",
    "Cristóbal",
    "Dagoberto",
    "Farid",
    "Jerónimo",
    "Kevin",
    "Lisandro",
    "Milton",
    "Yamid",
    "Leonel",
    "Uriel",
)


def fold_char(c: str) -> str:
    c = CONFUSABLES.get(c, c)
    base = unicodedata.normalize("NFD", c)[:1] or c
    low = base.lower()
    return low[0] if low else base


def fold(s: str) -> str:
    """Lowercase and strip accents character by character, so offsets stay aligned."""
    return "".join(fold_char(c) for c in s)


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


FEMALE = frozenset(fold(n) for n in FEMALE_GIVEN + EXTRA_FEMALE)
MALE = frozenset(fold(n) for n in MALE_GIVEN + EXTRA_MALE)
GIVEN = FEMALE | MALE
NEUTRAL_ENDINGS = ("ista", "euta", "iatra")
NEUTRAL_WORDS = frozenset({"piloto"})


@dataclass(frozen=True)
class NameParts:
    given: tuple[str, ...]
    surnames: tuple[str, ...]

    @property
    def full(self) -> str:
        return " ".join(self.given + self.surnames)

    @property
    def short(self) -> str:
        return " ".join(self.given + self.surnames[:1])

    @property
    def first(self) -> str:
        return " ".join(self.given)


@lru_cache(maxsize=10_000)
def split_name(full_name: str) -> NameParts:
    """Colombian convention: one or two given names followed by one or two surnames."""
    tokens = tuple(full_name.split())
    if len(tokens) <= 1:
        return NameParts(tokens, ())
    if len(tokens) == 2:
        return NameParts(tokens[:1], tokens[1:])
    if len(tokens) >= 4:
        return NameParts(tokens[:2], tokens[2:])
    second_is_given = fold(tokens[1]) in GIVEN and fold(tokens[2]) not in GIVEN
    if second_is_given:
        return NameParts(tokens[:2], tokens[2:])
    return NameParts(tokens[:1], tokens[1:])


def infer_gender(given: str, hint: str = "") -> str:
    """Return 'F' or 'M'. `hint` is a gendered word such as an occupation or job title."""
    hinted = _gender_from_hint(hint)
    if hinted:
        return hinted
    first = fold(given.split()[0]) if given.split() else ""
    if first in FEMALE and first not in MALE:
        return "F"
    if first in MALE:
        return "M"
    return "F" if first.endswith("a") else "M"


def _gender_from_hint(hint: str) -> str:
    words = fold(hint).split()
    head = words[0] if words else ""
    if head.endswith("a") and not head.endswith(NEUTRAL_ENDINGS):
        return "F"
    if head.endswith(("o", "or")) and head not in NEUTRAL_WORDS:
        return "M"
    return ""
