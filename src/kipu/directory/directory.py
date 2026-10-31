"""In-memory view of persons for one run, synchronized with the persistent registry."""

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from kipu.directory.names import NameParts, fold, infer_gender, split_name
from kipu.directory.registry import Registry
from kipu.directory.seed import STRONG, PersonSeed, norm_identifier
from kipu.identity.resolve import resolve

log = logging.getLogger(__name__)

KIND_PRIORITY = {"client": 0, "employee": 1, "client_user": 2, "employee_user": 3}
EXCLUSIVE_TYPES = ("cedula", "cliente_id", "kp_id", "uid")
STORED_TYPES = (
    *STRONG,
    "address",
    "birth_iso",
    "passport",
    "plate",
    "handle",
    "radicado",
    "account",
)


@dataclass
class Person:
    person_id: str
    kind: str
    gender: str
    name: NameParts
    names: set[str] = field(default_factory=set)
    identifiers: dict[str, set[str]] = field(default_factory=dict)
    attrs: dict[str, str] = field(default_factory=dict)

    def handles(self) -> set[str]:
        """Email local parts (`maria.lopez`), used as @handles and bare agent ids."""
        return {
            e.split("@", 1)[0].lower() for e in self.identifiers.get("email", set()) if "@" in e
        }


class Directory:
    def __init__(self, registry: Registry) -> None:
        self.registry = registry
        self.persons: dict[str, Person] = {}
        self.by_id: dict[tuple[str, str], str] = {}
        self.claimed: set[str] = set()
        self.created: set[str] = set()

    def add_seeds(self, seeds: list[PersonSeed], texts_by_uid: dict[str, str]) -> None:
        for cluster in resolve(seeds, texts_by_uid):
            named = [s for s in cluster if s.full_name]
            if not named and not any(s.identifiers for s in cluster):
                continue
            self._sync_cluster(cluster)

    def _sync_cluster(self, cluster: list[PersonSeed]) -> None:
        primary = min(cluster, key=lambda s: (KIND_PRIORITY.get(s.kind, 9), not s.full_name))
        kind = "employee" if any(s.kind.startswith("employee") for s in cluster) else "client"
        hint = next((s.gender_hint for s in cluster if s.kind in ("client", "employee")), "")
        name = split_name(primary.full_name)
        gender = infer_gender(name.first, hint or primary.gender_hint)
        attrs: dict[str, str] = {}
        # The primary (oldest) row is applied last: a re-listed client keeps its first profile.
        for seed in sorted(cluster, key=lambda s: s is primary):
            attrs.update({k: v for k, v in seed.attrs.items() if v})
        identifiers: dict[str, set[str]] = {}
        for seed in cluster:
            for id_type, values in seed.identifiers.items():
                identifiers.setdefault(id_type, set()).update(values)
        person_id = self._existing_person(identifiers)
        if person_id is None and not self._can_hold_any(identifiers):
            # Every identifier already belongs to someone: a new person would hold nothing and
            # be created again on every run.
            log.warning("cluster %s has no identifier of its own; no person created", primary.key)
            return
        if person_id is None:
            person_id = self.registry.new_person(kind, gender, name.full, attrs)
            self.created.add(person_id)
        self.claimed.add(person_id)
        self.registry.update_profile(person_id, name.full, attrs)
        person = self.persons.get(person_id) or Person(person_id, kind, gender, name)
        person.names.update(s.full_name for s in cluster if s.full_name)
        person.attrs.update(attrs)
        for id_type, values in identifiers.items():
            for raw in values:
                self.attach(person, id_type, raw, primary.key)
        self.persons[person_id] = person

    def _existing_person(self, identifiers: dict[str, set[str]]) -> str | None:
        """Registry person for this cluster: shares a strong key, is not claimed by another
        cluster of this run, and does not contradict it (different cédula or master row)."""
        normalized = {
            t: {norm_identifier(t, v) for v in values} for t, values in identifiers.items()
        }
        owners = sorted(
            {
                owner
                for id_type, values in normalized.items()
                if id_type in STRONG
                for value in values
                if (owner := self.registry.owner(id_type, value))
                and owner not in self.claimed
                and not self._contradicts(owner, normalized)
            }
        )
        if len(owners) > 1:
            log.warning("cluster maps to %d registry persons; keeping the oldest", len(owners))
        return owners[0] if owners else None

    def _can_hold_any(self, identifiers: dict[str, set[str]]) -> bool:
        return any(
            (value := norm_identifier(id_type, raw))
            and self.owner_of(id_type, value) is None
            and self.registry.owner(id_type, value) is None
            for id_type, values in identifiers.items()
            if id_type in STORED_TYPES
            for raw in values
        )

    def _contradicts(self, person_id: str, normalized: dict[str, set[str]]) -> bool:
        for id_type in EXCLUSIVE_TYPES:
            stored = self.registry.values_of(person_id, id_type)
            if stored and normalized.get(id_type) and not stored & normalized[id_type]:
                return True
        return False

    def attach(self, person: Person, id_type: str, raw: str, source: str) -> bool:
        """Link an identifier to a person of this run. A registry row owned by someone outside
        this run (e.g. an older corpus) cannot move, but the in-run link still holds."""
        value = norm_identifier(id_type, raw)
        if not value:
            return False
        owner = self.owner_of(id_type, value)
        if owner and owner != person.person_id:
            return False
        if id_type in STORED_TYPES:
            self.registry.add_identifier(person.person_id, id_type, value, raw, source)
        person.identifiers.setdefault(id_type, set()).add(raw)
        self.by_id[(id_type, value)] = person.person_id
        if id_type == "email" and "@" in raw:
            self.attach(person, "handle", raw.split("@", 1)[0], source)
        return True

    def owner_of(self, id_type: str, raw: str) -> str | None:
        """Owner among the persons of this run only, so every owner can be rendered."""
        value = norm_identifier(id_type, raw)
        owner = self.by_id.get((id_type, value)) or self.registry.owner(id_type, value)
        return owner if owner in self.persons else None

    def external_person(self, name: str, key: str, gender: str = "") -> Person:
        """A person found only in text (no master row). `key` makes homonyms distinct."""
        tokens = re.findall(r"[a-z0-9]+", fold(name))
        lookup = f"{' '.join(tokens)}|{key}"
        existing = self.registry.owner("external", lookup)
        if existing and existing in self.persons:
            return self.persons[existing]
        name = " ".join(name.split())
        if name and " " not in name:
            name = " ".join(w.capitalize() for w in re.split(r"[_.\-]+", name) if w)
        parts = split_name(name)
        gender = gender or infer_gender(parts.first)
        person_id, created = self.registry.external_person(lookup, gender, parts.full, name)
        if created:
            self.created.add(person_id)
        person = Person(person_id, "external", gender, parts)
        if name:
            person.names.add(name)
        self.by_id[("external", lookup)] = person_id
        self.persons[person_id] = person
        return person

    @classmethod
    def from_registry(cls, registry: Registry) -> "Directory":
        directory = cls(registry)
        for row in registry.persons():
            directory.persons[row["person_id"]] = _person_from_row(row)
        for person_id, id_type, value_norm, value_raw in registry.identifiers():
            person = directory.persons.get(person_id)
            if person is None:
                continue
            directory.by_id[(id_type, value_norm)] = person_id
            if id_type != "external":
                person.identifiers.setdefault(id_type, set()).add(value_raw)
        return directory


def _person_from_row(row: dict[str, Any]) -> Person:
    name = split_name(row["name"])
    person = Person(row["person_id"], row["kind"], row["gender"], name, attrs=row["attrs"])
    if row["name"]:
        person.names.add(row["name"])
    return person
