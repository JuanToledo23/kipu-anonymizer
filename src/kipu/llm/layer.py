"""The LLM layer on top of the rules: the models propose, the code verifies, replaces, audits.

Every pass is a full, deterministic rules pass with the accepted spans added through the
detector hook. Model answers are gathered before a pass and applied in record order, so the
output does not depend on which call finished first.
"""

import logging
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kipu.anonymizer import Anonymizer
from kipu.config import LLMSettings
from kipu.ingest.records import ChatRecord, DocRecord, TableRecord
from kipu.llm.client import LLMClient, LoopThread, loop_thread
from kipu.llm.extract import EXTRACT_SYSTEM, Unit, chat_units, doc_units, run_units, table_units
from kipu.llm.generalize import classify_occupations, generalize_phrases
from kipu.llm.identity import (
    HOMONYM_SCHEMA,
    HOMONYM_SYSTEM,
    LLMMentions,
    checked_verdict,
    compact,
    homonym_prompt,
    phrase_key,
)
from kipu.llm.omissions import (
    OMISSION_SYSTEM,
    OmissionVerifier,
    audit_spans,
    output_units,
    suspect_records,
)
from kipu.llm.verify import Dispute, Key, Span, Verifier, adjudicate, by_surface

log = logging.getLogger(__name__)

MAX_OMISSION_ITERATIONS = 2
Outputs = dict[str, list[dict[str, Any]]]
HookFactory = Callable[[Anonymizer], LLMMentions]
# One anonymization pass: (hook factory or None for rules only, occupation sectors).
RunPass = Callable[[HookFactory | None, dict[str, str]], tuple[Anonymizer, Outputs]]


@dataclass
class LayerReport:
    mentions: Counter = field(default_factory=Counter)
    persons_created: int = 0
    omissions: list[int] = field(default_factory=list)
    audit_spans: list[int] = field(default_factory=list)
    strong_omissions: dict[str, Any] = field(default_factory=dict)
    passes: int = 0
    seconds: float = 0.0
    capped: bool = False
    usage: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mentions": {
                f"{label}/{source}": n for (label, source), n in sorted(self.mentions.items())
            },
            "persons_created": self.persons_created,
            "omissions": self.omissions,
            "audit_spans": self.audit_spans,
            "strong_omissions": self.strong_omissions,
            "passes": self.passes,
            "seconds": round(self.seconds, 2),
            "capped": self.capped,
            **self.usage,
        }


class LLMLayer:
    def __init__(
        self,
        settings: LLMSettings,
        cache_dir: Path,
        client: LLMClient | None = None,
        loop: LoopThread | None = None,
    ) -> None:
        self.settings = settings
        self.client = client or LLMClient(settings, cache_dir)
        self.loop = loop or loop_thread()
        self.report = LayerReport()
        self.quasi: dict[str, str] = {}
        self.created: set[str] = set()
        self.final_created: set[str] = set()

    def refine_corpus(
        self,
        chats: list[ChatRecord],
        docs: list[DocRecord],
        tables: list[TableRecord],
        run_pass: RunPass,
    ) -> tuple[Anonymizer, Outputs]:
        """Batch run: both extractors, strong adjudication, omissions, then the final pass."""
        started = time.monotonic()
        s = self.settings
        anonymizer, outputs = run_pass(None, {})
        units = chat_units(chats) + doc_units(docs) + table_units(tables)
        verifier = self._verifier(anonymizer, [s.primary, s.second], ["primary", "second"])
        results = self.loop.run(
            run_units(self.client, units, [s.primary, s.second], EXTRACT_SYSTEM)
        )
        accepted = self._decide(units, results, verifier)
        occupations = [r.columns.get("ocupacion", "") for r in tables if r.table == "clientes"]
        sectors = self.loop.run(classify_occupations(self.client, s.primary, occupations))
        raw = raw_surfaces(chats, docs, tables)
        hooks: list[LLMMentions] = []
        for iteration in range(MAX_OMISSION_ITERATIONS + 1):
            self._generalize(accepted)
            anonymizer, outputs = run_pass(self._hook_factory(accepted, hooks, judge=True), sectors)
            if iteration == MAX_OMISSION_ITERATIONS:
                break
            omitted, leaked = self._omissions(anonymizer, outputs, raw)
            self.report.omissions.append(len(omitted))
            self.report.audit_spans.append(len(leaked))
            log.info(
                "omissions iteration %d: %d new spans, %d from the auditor",
                iteration + 1,
                len(omitted),
                len(leaked),
            )
            found = omitted + leaked
            if not found:
                break
            accepted += found
        found = self._strong_omissions(anonymizer, outputs, raw)
        if found:
            accepted += found
            self._generalize(accepted)
            anonymizer, outputs = run_pass(self._hook_factory(accepted, hooks, judge=True), sectors)
        self._finish(anonymizer, hooks, started)
        return anonymizer, outputs

    def refine_batch(
        self, source: str, records: list[Any], run_pass: RunPass
    ) -> tuple[Anonymizer, Outputs]:
        """/anonymize: primary model only, within the time budget, no strong model."""
        started = time.monotonic()
        s = self.settings
        anonymizer, outputs = run_pass(None, {})
        builder = {"chats": chat_units, "docs": doc_units, "tables": table_units}[source]
        units = builder(records)
        verifier = self._verifier(anonymizer, [s.primary], ["primary"])
        results = self.loop.run(
            run_units(self.client, units, [s.primary], EXTRACT_SYSTEM, budget=s.anonymize_budget)
        )
        accepted = []
        for unit in units:
            found, _ = verifier.classify(unit, results.get(unit.unit_id, {}), single=True)
            accepted += found
        hooks: list[LLMMentions] = []
        if accepted:
            anonymizer, outputs = run_pass(self._hook_factory(accepted, hooks, judge=False), {})
        self._finish(anonymizer, hooks, started)
        return anonymizer, outputs

    def _verifier(self, anonymizer: Anonymizer, models: list[str], names: list[str]) -> Verifier:
        covered = {k: [(m.start, m.end) for m in ms] for k, ms in anonymizer.spans.items()}
        return Verifier(anonymizer.index, anonymizer.kept, covered, models, names)

    def _decide(
        self, units: list[Unit], results: dict[str, dict[str, list[Any]]], verifier: Verifier
    ) -> list[Span]:
        accepted: list[Span] = []
        disputes: list[Dispute] = []
        for unit in units:
            found, disputed = verifier.classify(unit, results.get(unit.unit_id, {}))
            accepted += found
            if disputed:
                disputes.append(Dispute(unit, disputed))
        log.info(
            "LLM candidates: %d spans accepted, %d units to adjudicate",
            len(accepted),
            len(disputes),
        )
        if disputes:
            accepted += self.loop.run(
                adjudicate(self.client, self.settings.strong, verifier, disputes)
            )
        return accepted

    def _generalize(self, accepted: list[Span]) -> None:
        phrases = [
            s.text
            for s in accepted
            if s.label == "QUASI_ID" and phrase_key(s.text) not in self.quasi
        ]
        if phrases:
            self.quasi.update(
                self.loop.run(generalize_phrases(self.client, self.settings.primary, phrases))
            )

    def _hook_factory(
        self, accepted: list[Span], hooks: list[LLMMentions], judge: bool
    ) -> HookFactory:
        spans = by_surface(accepted)

        def factory(anonymizer: Anonymizer) -> LLMMentions:
            hook = LLMMentions(
                spans,
                anonymizer.directory,
                anonymizer.index,
                anonymizer.detector.surrogates,
                self.quasi,
                self._judge if judge else None,
            )
            hooks.append(hook)
            return hook

        return factory

    def _judge(
        self, name: str, candidates: list[str], text: str, evidence: dict[str, list[str]]
    ) -> str | None:
        user = homonym_prompt(name, candidates, text, evidence)
        data = self.loop.run(
            self.client.complete(
                self.settings.strong, HOMONYM_SYSTEM, user, HOMONYM_SCHEMA, "homonym"
            )
        )
        return checked_verdict(data, candidates, text, evidence)

    def _omissions(
        self, anonymizer: Anonymizer, outputs: Outputs, raw: dict[Key, str]
    ) -> tuple[list[Span], list[Span]]:
        """(spans the model found still visible, leaked forms the auditor found)."""
        model = self.settings.second
        units = output_units(outputs)
        verifier = OmissionVerifier(anonymizer.index, anonymizer.kept, model, raw, anonymizer.spans)
        results = self.loop.run(run_units(self.client, units, [model], OMISSION_SYSTEM))
        found = self._decide(units, results, verifier)
        records = [r for recs in outputs.values() for r in recs]
        entities = anonymizer.entities(records)
        directory = anonymizer.directory
        leaked = audit_spans(
            records,
            entities,
            raw,
            anonymizer.spans,
            lambda form: directory.owner_of("account", compact(form)) is not None,
        )
        return found, leaked

    def _strong_omissions(
        self, anonymizer: Anonymizer, outputs: Outputs, raw: dict[Key, str]
    ) -> list[Span]:
        """The strong model re-reads only the records where a person-like bigram is still
        visible (bounded); it is the adjudicator, so its finds are not disputed again."""
        records = set(suspect_records(raw, anonymizer.spans))
        units = [
            u
            for u in output_units(outputs)
            if any(t.record_id in records for item in u.items for t in item.targets)
        ]
        model = self.settings.strong
        verifier = OmissionVerifier(
            anonymizer.index, anonymizer.kept, model, raw, anonymizer.spans, "omission_strong"
        )
        results = self.loop.run(run_units(self.client, units, [model], OMISSION_SYSTEM))
        found: list[Span] = []
        for unit in units:
            accepted, _ = verifier.classify(unit, results.get(unit.unit_id, {}), single=True)
            found += accepted
        labels = Counter(s.label for s in found)
        self.report.strong_omissions = {
            "records": len(records),
            "units": len(units),
            "spans": len(found),
            "labels": dict(sorted(labels.items())),
        }
        log.info("strong omissions: %s", self.report.strong_omissions)
        return found

    def _finish(self, anonymizer: Anonymizer, hooks: list[LLMMentions], started: float) -> None:
        final = hooks[-1] if hooks else None
        persons: set[str] = set()
        for hook in hooks:
            self.created |= hook.created
        for (record_id, field_name), mentions in anonymizer.spans.items():
            for m in mentions:
                if m.source != "llm" or final is None:
                    continue
                source = final.sources.get((record_id, field_name, m.start), "?")
                self.report.mentions[(m.label, source)] += 1
                if m.person_id:
                    persons.add(m.person_id)
        self.final_created = self.created & persons
        self.report.persons_created = len(self.final_created)
        self.report.passes = len(hooks) + 1
        self.report.seconds = time.monotonic() - started
        self.report.capped = self.client.capped
        self.report.usage = self.client.totals()


def raw_surfaces(
    chats: list[ChatRecord], docs: list[DocRecord], tables: list[TableRecord]
) -> dict[Key, str]:
    """Every surface the anonymizer reads, in raw form (the omission pass maps back here)."""
    raw: dict[Key, str] = {}
    for r in chats:
        raw[(r.id, "text")] = r.text
        for i, a in enumerate(r.attachments):
            raw[(r.id, f"attachments.{i}")] = a.filename
    for r in docs:
        raw[(r.id, "filename")] = r.filename
        raw[(r.id, "title")] = r.title
        raw[(r.id, "body")] = r.body
        for k, v in r.metadata.items():
            raw[(r.id, f"metadata.{k}")] = v
    for r in tables:
        for k, v in r.columns.items():
            raw[(r.id, f"columns.{k}")] = v
    return raw
