"""Omissions pass: a model re-reads the anonymized text for anything personal left behind.

The model sees our realistic pseudonyms too and may list them; a candidate only counts if its
text occurs in the raw surface outside every span already replaced or kept, which is exactly
what is still visible in the output.
"""

import re
from collections.abc import Callable
from typing import Any

from kipu.audit.auditor import audit
from kipu.detect.mention import Mention
from kipu.detect.names import KeptMatcher, NameIndex
from kipu.directory.names import GIVEN, fold
from kipu.ingest.records import ChatRecord, DocRecord, TableRecord
from kipu.llm.extract import (
    OUTPUT_RULES,
    SECURITY,
    Candidate,
    Unit,
    chat_units,
    doc_units,
    table_units,
)
from kipu.llm.verify import Key, Span, Verifier, context_matches, word_pattern

OMISSION_SYSTEM = f"""Revisas textos en español de Colombia (soporte de Kipu Pay) que YA \
pasaron por un anonimizador: los nombres, correos, teléfonos y documentos se reemplazaron por \
seudónimos realistas.

{SECURITY}

Cada segmento empieza con [n], su número de item; [author: Ux] es un alias del autor.

Lista solo lo que pueda haber quedado sin anonimizar: cualquier mención de una persona natural \
que parezca real (nombre, apodo, apellido, nombre en minúscula o dentro de un archivo o URL) y \
cualquier identificador personal (PERSONAL_ADDRESS, BIRTH_DATE, AGE, PASSPORT, ACCOUNT, PLATE, \
EMAIL deletreado, QUASI_ID). Si no sabes si un nombre es un seudónimo, lístalo: el código \
descarta los seudónimos. No listes organizaciones, lugares, marcas ni figuras públicas.

{OUTPUT_RULES}"""
# Two capitalized words, the first a known given name: a person the rules may have missed.
PERSON_BIGRAM = re.compile(r"\b([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)[ \t]+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\b")
MAX_STRONG_RECORDS = 150


def output_units(outputs: dict[str, list[dict[str, Any]]]) -> list[Unit]:
    """Units over the anonymized records (same chunking as the extraction)."""
    return (
        chat_units(ChatRecord(**r) for r in outputs.get("chats", []))
        + doc_units(DocRecord(**r) for r in outputs.get("docs", []))
        + table_units(TableRecord(**r) for r in outputs.get("tables", []))
    )


class OmissionVerifier(Verifier):
    """Same decisions as `Verifier`, but candidates found in output text are placed in the raw
    surface, outside the final spans of the last pass."""

    def __init__(
        self,
        index: NameIndex,
        kept: KeptMatcher,
        model: str,
        raw: dict[Key, str],
        final: dict[Key, list[Mention]],
        source: str = "omission",
    ) -> None:
        super().__init__(index, kept, {}, [model], [source])
        self.raw = raw
        self.final = final

    def spans(self, unit: Unit, c: Candidate, source: str) -> list[Span]:
        text = c.text.strip()
        if len(text) < 2:
            return []
        pattern = word_pattern(text)
        items = [unit.items[c.item]] if c.item >= 0 else unit.items
        if not any(pattern.search(item.text) for item in items):
            items = unit.items
        found: list[Span] = []
        for item in items:
            out_hits = [(0, m.start(), m.end()) for m in pattern.finditer(item.text)]
            if not out_hits:
                continue
            for t in item.targets:
                raw_hits = self._visible(t.record_id, t.field, pattern)
                if self._context_only(c, text):
                    matched = [k for k, h in enumerate(out_hits) if context_matches(item, h, c)]
                    exact = len(raw_hits) == len(out_hits) and len(matched) == 1
                    raw_hits = [raw_hits[matched[0]]] if exact else []
                raw_text = self.raw[(t.record_id, t.field)]
                found += [
                    Span(
                        t.record_id, t.field, s, e, raw_text[s:e], c.label, c.gender, c.role, source
                    )
                    for s, e in raw_hits
                ]
        return found

    def _visible(
        self, record_id: str, field: str, pattern: re.Pattern[str]
    ) -> list[tuple[int, int]]:
        raw_text = self.raw.get((record_id, field))
        if raw_text is None:
            return []
        taken = [(m.start, m.end) for m in self.final.get((record_id, field), [])]
        return [
            (m.start(), m.end())
            for m in pattern.finditer(raw_text)
            if not any(m.start() < e and s < m.end() for s, e in taken)
        ]


def suspect_records(
    raw: dict[Key, str], final: dict[Key, list[Mention]], limit: int = MAX_STRONG_RECORDS
) -> list[str]:
    """Records whose raw text still shows a person-like bigram outside every span the last
    pass replaced or kept (gazetteer terms are kept spans), first `limit` by id."""
    suspects = set()
    for (record_id, field), text in raw.items():
        taken = [(m.start, m.end) for m in final.get((record_id, field), [])]
        for m in PERSON_BIGRAM.finditer(text):
            visible = not any(m.start() < e and s < m.end() for s, e in taken)
            if visible and fold(m.group(1)) in GIVEN:
                suspects.add(record_id)
                break
    return sorted(suspects)[:limit]


def audit_spans(
    records: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    raw: dict[Key, str],
    final: dict[Key, list[Mention]],
    is_account: Callable[[str], bool],
) -> list[Span]:
    """Name and account forms the auditor still finds in the output become spans of their
    person in the records where they leak (a form the model linked once must go everywhere)."""
    owners = {
        form: e["entity_id"]
        for e in entities
        if not e.get("kept")
        for form in e.get("originals", [])
    }
    keys_by_record: dict[str, list[Key]] = {}
    for key in raw:
        keys_by_record.setdefault(key[0], []).append(key)
    spans = []
    for record_id, forms in audit(records, entities).items():
        for form in sorted(set(forms)):
            if form not in owners:
                continue
            if re.fullmatch(r"[^\W\d_]+(?:[ \-'][^\W\d_]+)*", form):
                label = "PERSON"
            elif is_account(form):
                label = "ACCOUNT"
            else:
                continue
            pattern = word_pattern(fold(form))
            for key in keys_by_record.get(record_id, []):
                text = raw[key]
                taken = [(m.start, m.end) for m in final.get(key, [])]
                for m in pattern.finditer(fold(text)):
                    if any(m.start() < e and s < m.end() for s, e in taken):
                        continue
                    spans.append(
                        Span(
                            key[0],
                            key[1],
                            m.start(),
                            m.end(),
                            text[m.start() : m.end()],
                            label,
                            source="audit",
                            person_id=owners[form],
                        )
                    )
    return spans
