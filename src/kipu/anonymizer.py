"""Detect → resolve → replace for normalized records. Shared by the batch pipeline and the API."""

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from kipu.audit.auditor import record_text
from kipu.detect.engine import Detector, Group, Surface
from kipu.detect.mention import IDENTIFIER_LABELS, Mention
from kipu.detect.names import TOKEN, KeptMatcher, NameIndex
from kipu.detect.patterns import find_plates
from kipu.detect.quasi import QuasiLexicon
from kipu.directory.directory import Directory
from kipu.directory.names import fold
from kipu.directory.registry import Registry
from kipu.directory.seed import norm_identifier
from kipu.entities import EntityLedger, build_entities
from kipu.ingest.records import ChatRecord, DocRecord, TableRecord
from kipu.pseudo.surrogates import SurrogateFactory, SurrogateName
from kipu.replace.generalize import sector_for, swap_barrio
from kipu.replace.render import Renderer, apply_mentions

FREE_TEXT_COLUMNS = {"notas", "descripcion", "respuesta"}
UNTOUCHED_COLUMNS = {
    "cliente_id",
    "tx_id",
    "radicado",
    "id",
    "tipo_doc",
    "documento_tipo",
    "ciudad",
    "fecha",
    "fecha_vinculacion",
    "tipo",
    "monto",
    "canal",
    "estado",
    "cargo",
    "ingreso",
}
GENERALIZED_COLUMNS = {"barrio", "ocupacion"}
ROW_OWNER_KEYS = {"clientes": "cliente_id", "pqrs": "cliente_id", "transacciones": "cliente_id"}
FORM_LABELS = {"NAME", *IDENTIFIER_LABELS}


class Anonymizer:
    def __init__(
        self,
        registry: Registry,
        directory: Directory,
        secret: str,
        live: bool = False,
        sectors: dict[str, str] | None = None,
    ) -> None:
        """`live` (the API): values we already wrote as surrogates are left untouched.
        `sectors`: occupations outside the mapping table, classified by the LLM layer."""
        self.registry = registry
        self.directory = directory
        self.secret = secret
        self.live = live
        self.index = NameIndex(directory.persons.values())
        self.kept = KeptMatcher()
        surrogates = _surrogate_values(registry) if live else None
        self.sectors = sectors or {}
        quasi = QuasiLexicon(directory.persons.values(), self.sectors, self.kept.terms.values())
        self.detector = Detector(
            directory, self.index, self.kept, surrogates=surrogates, quasi=quasi
        )
        forbidden_names, forbidden_tokens, self.observed = _forbidden(directory)
        self.factory = SurrogateFactory(
            registry,
            secret,
            forbidden_names,
            forbidden_tokens,
            lambda field, value: _is_real(directory, self.observed, field, value),
        )
        self.renderer = Renderer(self.factory, directory)
        self.ledger = EntityLedger()
        self.spans: dict[tuple[str, str], list[Mention]] = {}

    def observe(self, texts: Iterable[str]) -> None:
        """Plates seen anywhere in the input, so no surrogate plate reproduces one of them."""
        for text in texts:
            self.observed.update(m.value for m in find_plates(text))

    def _apply(self, group: Group) -> dict[tuple[str, str], str]:
        """Detect over the group and return the replaced text of every surface."""
        found = self.detector.detect(group)
        out = {}
        for surface in group.surfaces:
            mentions = found[(surface.record_id, surface.field)]
            self.spans[(surface.record_id, surface.field)] = mentions
            self._record(mentions, surface.record_id)
            out[(surface.record_id, surface.field)] = apply_mentions(
                surface.text, mentions, self.renderer
            )
        return out

    def _record(self, mentions: list[Mention], record_id: str) -> None:
        for m in mentions:
            if m.label == "KEPT":
                self.ledger.add_kept(m.value)
            elif m.person_id and m.label in FORM_LABELS:
                self.ledger.add(m.person_id, m.text, record_id)
            elif m.person_id and m.label == "SLACK":
                self.ledger.records[m.person_id].add(record_id)

    def chats(
        self, records: list[ChatRecord], author_ids: dict[str, str | None] | None = None
    ) -> list[dict[str, Any]]:
        author_ids = author_ids or {}
        threads: dict[str, list[ChatRecord]] = defaultdict(list)
        for record in records:
            threads[record.thread_id].append(record)
        results: dict[str, dict[str, Any]] = {}
        for thread in threads.values():
            thread.sort(key=lambda r: float(r.ts) if _is_number(r.ts) else 0.0)
            authors = {r.id: author_ids.get(r.id) or self.author_person(r.author) for r in thread}
            surfaces = []
            for r in thread:
                surfaces.append(Surface(r.id, "text", r.text, free_text=True))
                surfaces += [
                    Surface(r.id, f"attachments.{i}", a.filename)
                    for i, a in enumerate(r.attachments)
                ]
            replaced = self._apply(
                Group(surfaces, authors=authors, kind="chat", scope=thread[0].thread_id)
            )
            for r in thread:
                results[r.id] = self._chat_output(r, authors.get(r.id), replaced)
        return [results[r.id] for r in records]

    def _chat_output(
        self, r: ChatRecord, author: str | None, replaced: dict[tuple[str, str], str]
    ) -> dict[str, Any]:
        author_name = r.author
        if author:
            self.ledger.add(author, r.author, r.id)
            author_name = self.renderer.short_name(self.directory.persons[author])
        return {
            "id": r.id,
            "thread_id": r.thread_id,
            "author": author_name,
            "ts": r.ts,
            "text": replaced[(r.id, "text")],
            "attachments": [
                {"filename": replaced[(r.id, f"attachments.{i}")]}
                for i in range(len(r.attachments))
            ],
        }

    def author_person(self, author: str) -> str | None:
        """Person behind an author display name; unknown human names become new persons."""
        tokens = tuple(TOKEN.findall(fold(author)))
        if " ".join(tokens) in self.detector.surrogates:  # a pseudonym copied from our output
            return None
        hits = self.index.entries.get(tokens, {})
        strong = sorted(p for p, kind in hits.items() if kind in ("full", "short"))
        if strong:
            return strong[0]
        if len(tokens) >= 2 and tokens not in self.kept.terms:
            return self.directory.external_person(author, "").person_id
        return None

    def docs(self, records: list[DocRecord]) -> list[dict[str, Any]]:
        results = []
        for r in records:
            surfaces = [
                Surface(r.id, "filename", r.filename),
                Surface(r.id, "title", r.title),
                Surface(r.id, "body", r.body, free_text=True),
            ]
            surfaces += [Surface(r.id, f"metadata.{k}", v) for k, v in r.metadata.items()]
            replaced = self._apply(Group(surfaces, kind="doc", prefer="employee", scope=r.id))
            results.append(
                {
                    "id": r.id,
                    "format": r.format,
                    "filename": replaced[(r.id, "filename")],
                    "title": replaced[(r.id, "title")],
                    "body": replaced[(r.id, "body")],
                    "metadata": {k: replaced[(r.id, f"metadata.{k}")] for k in r.metadata},
                }
            )
        return results

    def tables(self, records: list[TableRecord]) -> list[dict[str, Any]]:
        return [self._table_row(r) for r in records]

    def _table_row(self, r: TableRecord) -> dict[str, Any]:
        owner = self._row_owner(r)
        surfaces = [
            Surface(r.id, f"columns.{k}", v, free_text=k in FREE_TEXT_COLUMNS)
            for k, v in r.columns.items()
            if v and k not in UNTOUCHED_COLUMNS and k not in GENERALIZED_COLUMNS
        ]
        prefer = "employee" if r.table == "empleados" else "client"
        replaced = self._apply(
            Group(surfaces, owner=owner, kind="table", prefer=prefer, scope=r.id)
        )
        columns = {}
        for key, value in r.columns.items():
            columns[key] = replaced.get((r.id, f"columns.{key}"), value)
        if owner and r.table == "clientes":
            self._generalize_row(owner, columns)
        if owner and r.table == "empleados" and columns.get("cargo"):
            # Cargo → its free-text family: with ciudad + ingreso it singles the employee out.
            columns["cargo"] = self.detector.quasi.whole_term(columns["cargo"])
        return {"id": r.id, "table": r.table, "columns": columns}

    def _row_owner(self, r: TableRecord) -> str | None:
        if r.table == "empleados":
            return self.directory.owner_of("kp_id", r.columns.get("id", ""))
        key = ROW_OWNER_KEYS.get(r.table)
        return self.directory.owner_of("cliente_id", r.columns.get(key, "")) if key else None

    def _generalize_row(self, owner: str, columns: dict[str, str]) -> None:
        if columns.get("ocupacion"):
            columns["ocupacion"] = sector_for(columns["ocupacion"], self.sectors)
        if columns.get("barrio"):
            columns["barrio"] = swap_barrio(
                self.secret, owner, columns.get("ciudad", ""), columns["barrio"]
            )

    def entities(self, outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        texts = {o["id"]: record_text(o) for o in outputs}
        return build_entities(self.ledger, self.renderer, texts, strict=not self.live)


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _is_real(directory: Directory, observed: set[str], field: str, value: str) -> bool:
    normalized = norm_identifier(field, value)
    return normalized in observed or directory.owner_of(field, normalized) is not None


def _surrogate_values(registry: Registry) -> set[str]:
    """Normalized surrogate names (short and full), emails, phones and cédulas."""
    values: set[str] = set()
    for field, value in registry.surrogate_values():
        if field == "name":
            name = SurrogateName.decode(value)
            values.update({fold(name.short), fold(name.full)})
        elif field in ("email", "phone", "cedula"):
            values.add(norm_identifier(field, value))
    values.discard("")
    return values


def _forbidden(directory: Directory) -> tuple[set[str], set[str], set[str]]:
    """Real names, name tokens and identifier values that no surrogate may reproduce."""
    names: set[str] = set()
    tokens: set[str] = set()
    observed: set[str] = set()
    for person in directory.persons.values():
        for full in person.names | {person.name.full}:
            names.add(fold(full))
            tokens.update(fold(full).split())
        names.add(fold(person.name.short))
        tokens.update(fold(person.attrs.get("display_name", "")).split())
        for id_type, values in person.identifiers.items():
            for value in values:
                observed.add(norm_identifier(id_type, value))
                observed.add(norm_identifier("cedula", value))
    return names, tokens, observed
