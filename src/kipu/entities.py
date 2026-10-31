"""Builds entities.json from what was detected and what was actually written."""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from kipu.audit.texto import contiene, norm
from kipu.replace.render import Renderer

KEPT_REASON = "organización, lugar, marca o figura pública; no es persona natural"
NAME_FIELDS = ("name", "name_full", "name_partial")
SINGLE_VALUE_FIELDS = (
    "email",
    "phone",
    "cedula",
    "birth_iso",
    "address",
    "plate",
    "passport",
    "account",
    "handle",
)


@dataclass
class EntityLedger:
    """Accumulates, per person, the original forms seen and the records they occurred in."""

    originals: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    records: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    kept: dict[str, None] = field(default_factory=dict)

    def add(self, person_id: str, form: str, record_id: str) -> None:
        if form.strip():
            self.originals[person_id].add(form)
        self.records[person_id].add(record_id)

    def add_kept(self, term: str) -> None:
        self.kept.setdefault(term, None)


def declared_pseudonyms(
    used: dict[str, list[str]], output_texts: list[str], canonical: str, strict: bool = True
) -> dict[str, str]:
    """One value per field. The name is always the canonical short form (given names + first
    surname). `strict` (entities.json) also requires every value to appear in the person's
    records, as the validator does; a person seen only by given name then declares that."""
    pseudonyms: dict[str, str] = {}
    if any(used.get(f) for f in NAME_FIELDS):
        if not strict or any(contiene(t, canonical) for t in output_texts):
            pseudonyms["name"] = canonical
        else:
            partial = sorted(used.get("name_partial", []), key=lambda n: (-len(n.split()), n))
            name = next((n for n in partial if any(contiene(t, n) for t in output_texts)), None)
            if name:
                pseudonyms["name"] = name
    for field_name in SINGLE_VALUE_FIELDS:
        values = used.get(field_name, [])
        if field_name == "birth_iso":
            values = sorted(values, key=lambda v: "-" not in v)
        chosen = next(
            (v for v in values if not strict or any(contiene(t, v) for t in output_texts)), None
        )
        if chosen:
            pseudonyms[field_name] = chosen
    handles = used.get("handle", [])
    if handles and "name" not in pseudonyms and "handle" not in pseudonyms:
        pseudonyms["handle"] = handles[0]
    return pseudonyms


def build_entities(
    ledger: EntityLedger,
    renderer: Renderer,
    texts_by_record: dict[str, str],
    strict: bool = True,
) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    for person_id in sorted(ledger.originals, key=_sort_key):
        records = sorted(ledger.records[person_id])
        texts = [texts_by_record[r] for r in records if r in texts_by_record]
        used = _oldest_first(renderer, person_id)
        canonical = (
            renderer.factory.name(renderer.directory.persons[person_id]).short
            if any(used.get(f) for f in NAME_FIELDS)
            else ""
        )
        pseudonyms = declared_pseudonyms(used, texts, canonical, strict)
        if not pseudonyms:
            continue
        entities.append(
            {
                "entity_id": person_id,
                "type": "person",
                "originals": sorted(ledger.originals[person_id]),
                "pseudonyms": pseudonyms,
                "records": records,
            }
        )
    entities = _drop_shared_originals(entities)
    for i, term in enumerate(sorted(ledger.kept), start=1):
        entities.append(
            {
                "entity_id": f"kept_{i:03d}",
                "type": "organization_or_place",
                "originals": [term],
                "pseudonyms": {},
                "kept": True,
                "reason": KEPT_REASON,
                "records": [],
            }
        )
    return entities


def _oldest_first(renderer: Renderer, person_id: str) -> dict[str, list[str]]:
    """Written values per field, oldest reservation first: a person who gains a second
    phone keeps declaring the first one."""
    rank = {key: i for i, key in enumerate(renderer.factory.registry.surrogate_order(person_id))}
    return {
        field_name: sorted(values, key=lambda v, f=field_name: rank.get((f, v), len(rank)))
        for field_name, values in renderer.used.get(person_id, {}).items()
    }


def _drop_shared_originals(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A form declared by two entities ("Luis Santander" for two homonyms) names neither;
    looking a person up by it would find both. It stays replaced in the records. An entity
    known only by shared forms (two third parties called "María") keeps them if it is the
    oldest to; a newer one is not declared, so growth never removes an entity."""
    owners: dict[str, int] = defaultdict(int)
    for entity in entities:
        for form in {norm(o) for o in entity["originals"]}:
            owners[form] += 1
    declared, taken = [], set()
    for entity in entities:
        forms = {norm(o) for o in entity["originals"]}
        kept = [o for o in entity["originals"] if owners[norm(o)] == 1]
        if kept:
            entity["originals"] = kept
        elif forms & taken:
            continue
        taken |= {norm(o) for o in entity["originals"]}
        declared.append(entity)
    return declared


def _sort_key(person_id: str) -> tuple[int, str]:
    suffix = person_id.split("_")[-1]
    return (int(suffix) if suffix.isdigit() else 10**9, person_id)
