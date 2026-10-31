"""Builds entities.json from what was detected and what was actually written."""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from kipu.audit.texto import contiene
from kipu.replace.render import Renderer

KEPT_REASON = "organización, lugar, marca o figura pública; no es persona natural"
SINGLE_VALUE_FIELDS = ("email", "phone", "cedula", "birth_iso", "address", "plate", "passport")


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


def declared_pseudonyms(used: dict[str, list[str]], output_texts: list[str]) -> dict[str, str]:
    """One value per field, chosen among values that really appear in the person's records."""
    pseudonyms: dict[str, str] = {}
    name = None
    for field_name in ("name", "name_full", "name_partial"):
        candidates = sorted(used.get(field_name, []), key=lambda n: (-len(n.split()), n))
        name = next((n for n in candidates if any(contiene(t, n) for t in output_texts)), None)
        if name:
            break
    if name:
        pseudonyms["name"] = name
    for field_name in SINGLE_VALUE_FIELDS:
        values = used.get(field_name, [])
        if field_name == "birth_iso":
            values = sorted(values, key=lambda v: "-" not in v)
        chosen = next((v for v in values if any(contiene(t, v) for t in output_texts)), None)
        if chosen:
            pseudonyms[field_name] = chosen
    handles = used.get("handle", [])
    if handles and "name" not in pseudonyms:
        pseudonyms["handle"] = handles[0]
    return pseudonyms


def build_entities(
    ledger: EntityLedger, renderer: Renderer, texts_by_record: dict[str, str]
) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    for person_id in sorted(ledger.originals, key=_sort_key):
        records = sorted(ledger.records[person_id])
        texts = [texts_by_record[r] for r in records if r in texts_by_record]
        pseudonyms = declared_pseudonyms(renderer.used.get(person_id, {}), texts)
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


def _sort_key(person_id: str) -> tuple[int, str]:
    suffix = person_id.split("_")[-1]
    return (int(suffix) if suffix.isdigit() else 10**9, person_id)
