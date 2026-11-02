"""Deterministic surrogates: seed = HMAC(secret, person_id|field|counter), reserved in SQLite.

A value is generated once per (person, field, original) and stored; later runs read it back,
so a person keeps the same pseudonym even if the generator or the name pools change.
"""

import datetime as dt
import hashlib
import hmac
import random
import string
from collections.abc import Callable
from dataclasses import dataclass

from kipu.directory.directory import Person
from kipu.directory.names import fold, strip_accents
from kipu.directory.registry import Registry
from kipu.pseudo.names_co import FEMALE_GIVEN, MALE_GIVEN, SURNAMES

PERSONAL_DOMAINS = ("gmail.com", "outlook.com", "yahoo.com", "icloud.com", "hotmail.com")
CORPORATE_DOMAIN = "kipupay.co"
STREET_TYPES = ("Calle", "Carrera", "Diagonal", "Transversal", "Avenida", "Cl", "Cra", "Tv")
ADDRESS_SUFFIXES = ("", "", " apto {n}", " of {n}", " casa {m}", " torre {t} apto {n}")
MAX_ATTEMPTS = 500


@dataclass(frozen=True)
class SurrogateName:
    given: tuple[str, ...]
    surnames: tuple[str, ...]

    @property
    def full(self) -> str:
        return " ".join(self.given + self.surnames)

    @property
    def short(self) -> str:
        return " ".join(self.given + self.surnames[:1])

    def encode(self) -> str:
        return " ".join(self.given) + "|" + " ".join(self.surnames)

    @classmethod
    def decode(cls, value: str) -> "SurrogateName":
        given, _, surnames = value.partition("|")
        return cls(tuple(given.split()), tuple(surnames.split()))


def ascii_token(s: str) -> str:
    return strip_accents(s).lower().replace(" ", "")


class SurrogateFactory:
    def __init__(
        self,
        registry: Registry,
        secret: str,
        forbidden_names: set[str],
        forbidden_tokens: set[str],
        is_real: Callable[[str, str], bool],
    ) -> None:
        self.registry = registry
        self.secret = secret.encode("utf-8")
        self.forbidden_names = forbidden_names
        self.is_real = is_real
        self.female = [n for n in FEMALE_GIVEN if fold(n) not in forbidden_tokens]
        self.male = [n for n in MALE_GIVEN if fold(n) not in forbidden_tokens]
        self.surnames = [s for s in SURNAMES if fold(s) not in forbidden_tokens]

    def rng(self, person_id: str, field: str, counter: int) -> random.Random:
        digest = hmac.new(self.secret, f"{person_id}|{field}|{counter}".encode(), hashlib.sha256)
        return random.Random(int.from_bytes(digest.digest(), "big"))

    def _reserve(self, person: Person, field: str, original: str, make, normalize=None) -> str:
        """Return the stored surrogate or generate candidates until one reserves cleanly."""
        stored = self.registry.surrogate(person.person_id, field, original)
        if stored is not None:
            return stored
        normalize = normalize or (lambda v: fold(v))
        for counter in range(MAX_ATTEMPTS):
            candidate = make(self.rng(person.person_id, f"{field}:{original}", counter), counter)
            if candidate is None or (field != "name" and self.is_real(field, candidate)):
                continue
            reserved = self.registry.reserve_surrogate(
                person.person_id, field, original, candidate, normalize(candidate)
            )
            if reserved is not None:
                return reserved
        raise RuntimeError(f"no free surrogate for field {field}")

    def name(self, person: Person) -> SurrogateName:
        original = person.name
        original_tokens = {fold(t) for t in original.given + original.surnames}
        original_initials = "".join(fold(t)[:1] for t in original.given + original.surnames[:1])
        pool = self.female if person.gender == "F" else self.male

        def make(rng: random.Random, _: int) -> str | None:
            given = rng.sample(pool, 2 if rng.random() < 0.25 else 1)
            surnames = rng.sample(self.surnames, 2)
            candidate = SurrogateName(tuple(given), tuple(surnames))
            tokens = {fold(t) for t in candidate.given + candidate.surnames}
            initials = "".join(fold(t)[:1] for t in candidate.given + candidate.surnames[:1])
            if tokens & original_tokens or initials == original_initials:
                return None
            if (
                fold(candidate.short) in self.forbidden_names
                or fold(candidate.full) in self.forbidden_names
            ):
                return None
            return candidate.encode()

        encoded = self._reserve(
            person, "name", "", make, normalize=lambda v: fold(SurrogateName.decode(v).short)
        )
        return SurrogateName.decode(encoded)

    def email(self, person: Person, original: str) -> str:
        name = self.name(person)
        domain = original.rsplit("@", 1)[-1].lower()
        given, surname = ascii_token(name.given[0]), ascii_token(name.surnames[0])

        def make(rng: random.Random, counter: int) -> str:
            suffix = (
                str(counter // len(PERSONAL_DOMAINS)) if counter >= len(PERSONAL_DOMAINS) else ""
            )
            if domain.endswith(CORPORATE_DOMAIN):
                local = f"{given}.{surname}{counter or ''}"
                pick = CORPORATE_DOMAIN
            else:
                local = f"{given[0]}{surname}{suffix}"
                pick = PERSONAL_DOMAINS[(rng.randrange(len(PERSONAL_DOMAINS)) + counter) % 5]
            # The local part doubles as a handle; a real person's handle must not reappear.
            return None if self.is_real("handle", local) else f"{local}@{pick}"

        return self._reserve(person, "email", original.lower(), make, normalize=str.lower)

    def handle(self, person: Person, original: str, email: str | None = None) -> str:
        """Same rule as the email local part; the surrogate email's own local part when the
        handle is the local part of one of the person's emails."""
        local = self.email(person, email).split("@", 1)[0] if email else None
        name = self.name(person)
        given, surname = ascii_token(name.given[0]), ascii_token(name.surnames[0])
        base = f"{given}.{surname}" if "." in original else f"{given[0]}{surname}"

        def make(rng: random.Random, counter: int) -> str:
            if local and counter == 0:
                return local
            return f"{base}{counter or ''}"

        return self._reserve(person, "handle", original.lower(), make, normalize=str.lower)

    def cedula(self, person: Person, original: str) -> str:
        def make(rng: random.Random, _: int) -> str | None:
            value = original[:2] + "".join(rng.choice(string.digits) for _ in original[2:])
            return None if value == original else value

        return self._reserve(person, "cedula", original, make, normalize=str)

    def phone(self, person: Person, original: str) -> str:
        def make(rng: random.Random, _: int) -> str | None:
            value = "3" + rng.choice("0125") + "".join(rng.choice(string.digits) for _ in range(8))
            return None if value == original else value

        return self._reserve(person, "phone", original, make, normalize=str)

    def birth(self, person: Person, original_iso: str) -> str:
        def make(rng: random.Random, _: int) -> str:
            year = int(original_iso[:4]) + rng.choice((-3, -2, -1, 1, 2, 3))
            return dt.date(year, rng.randint(1, 12), rng.randint(1, 28)).isoformat()

        return self._reserve(person, "birth_iso", original_iso, make, normalize=str)

    def address(self, person: Person, original: str) -> str:
        def make(rng: random.Random, _: int) -> str:
            street = rng.choice(STREET_TYPES)
            letter = rng.choice(("", "", "A", "B", " Bis", " Sur"))
            suffix = rng.choice(ADDRESS_SUFFIXES).format(
                n=rng.randint(101, 1804), m=rng.randint(1, 80), t=rng.randint(1, 8)
            )
            return (
                f"{street} {rng.randint(1, 170)}{letter} # {rng.randint(1, 120)}-"
                f"{rng.randint(1, 99)}{suffix}"
            )

        return self._reserve(person, "address", original, make)

    def plate(self, person: Person, original: str) -> str:
        """`original` is the compact plate (`AAA123`); the renderer restores the separator."""
        return self._reserve(person, "plate", original, self._same_shape(original), str.upper)

    def passport(self, person: Person, original: str) -> str:
        return self._reserve(person, "passport", original, self._same_shape(original), str.upper)

    def account(self, person: Person, original: str) -> str:
        return self._reserve(person, "account", original, self._same_shape(original), str.upper)

    @staticmethod
    def _same_shape(original: str):
        def make(rng: random.Random, _: int) -> str | None:
            value = "".join(
                rng.choice(string.ascii_uppercase)
                if c.isalpha()
                else rng.choice(string.digits)
                if c.isdigit()
                else c
                for c in original
            )
            return None if value == original else value

        return make
