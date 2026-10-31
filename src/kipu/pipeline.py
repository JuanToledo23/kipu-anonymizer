"""End-to-end run: source → records → directory → detect/replace → output/ (4 files)."""

import argparse
import logging
import shutil
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import orjson

from kipu.anonymizer import Anonymizer
from kipu.audit.auditor import audit
from kipu.config import Settings, load_settings
from kipu.directory.directory import Directory
from kipu.directory.registry import Registry
from kipu.directory.seed import seeds_from_tables, seeds_from_users
from kipu.ingest.parsers import RawCorpus, load_corpus
from kipu.ingest.source import DirSource, HttpSource, SourceClient

log = logging.getLogger("kipu.pipeline")

OUTPUT_FILES = ("chats.jsonl", "docs.jsonl", "tables.jsonl", "entities.json")


@dataclass
class RunReport:
    run_id: str
    records: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0
    persons: int = 0
    audit_hits: dict[str, list[str]] = field(default_factory=dict)
    missing_ids: dict[str, int] = field(default_factory=dict)
    published: bool = False
    pdf_xmp_docs: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0


def make_source(spec: str, settings: Settings, run_id: str) -> SourceClient:
    if spec.startswith("dir:"):
        return DirSource(spec.removeprefix("dir:"))
    if spec == "http" or spec.startswith("http:"):
        url = settings.fuentes_url if spec == "http" else spec.removeprefix("http:")
        if not url or not settings.fuentes_token:
            raise RuntimeError("FUENTES_URL and FUENTES_TOKEN are required for --source http")
        return http_source(url, settings.fuentes_token, settings, run_id)
    raise ValueError(f"unknown source spec: {spec}")


def http_source(url: str, token: str, settings: Settings, run_id: str) -> HttpSource:
    raw = settings.data_dir / "raw"
    return HttpSource(url, token, raw / run_id, etag_cache=raw / "etags.json")


def build_directory(
    corpus: RawCorpus, registry: Registry
) -> tuple[Directory, dict[str, str | None]]:
    texts_by_uid: dict[str, str] = defaultdict(str)
    uid_by_record: dict[str, str] = {}
    for messages in corpus.messages.values():
        for m in messages:
            texts_by_uid[m.get("user", "")] += "\n" + m.get("text", "")
            uid_by_record[m["client_msg_id"]] = m.get("user", "")
    directory = Directory(registry)
    directory.add_seeds(
        seeds_from_tables(corpus.tables) + seeds_from_users(corpus.users), texts_by_uid
    )
    authors = {rid: directory.owner_of("uid", uid) for rid, uid in uid_by_record.items()}
    return directory, authors


def run(source: SourceClient, out_dir: Path, settings: Settings) -> RunReport:
    started = time.monotonic()
    report = RunReport(run_id=uuid.uuid4().hex[:12])
    corpus = load_corpus(source)
    report.pdf_xmp_docs = sorted(corpus.pdf_xmp)
    registry = Registry(settings.registry_path)
    try:
        directory, authors = build_directory(corpus, registry)
        anonymizer = Anonymizer(registry, directory, settings.pseudonym_secret)
        outputs = {
            "chats": anonymizer.chats(corpus.chats, authors),
            "docs": anonymizer.docs(corpus.docs),
            "tables": anonymizer.tables(corpus.tables),
        }
        all_records = [r for recs in outputs.values() for r in recs]
        entities = anonymizer.entities(all_records)
        registry.record_forms(
            [
                (pid, form, rid)
                for pid, forms in anonymizer.ledger.originals.items()
                for form in forms
                for rid in anonymizer.ledger.records[pid]
            ][:200_000]
        )
        report.persons = sum(1 for e in entities if not e.get("kept"))
    finally:
        registry.close()
    report.records = {k: len(v) for k, v in outputs.items()}
    report.missing_ids = _missing(corpus.record_index, outputs)
    report.audit_hits = audit(all_records, entities)
    write_output(outputs, entities, out_dir)
    report.published = True
    report.seconds = round(time.monotonic() - started, 2)
    return report


def _missing(index: dict[str, list[str]] | None, outputs: dict[str, list[dict]]) -> dict[str, int]:
    if not index:
        return {}
    return {
        source: len(set(index.get(source, [])) - {r["id"] for r in records})
        for source, records in outputs.items()
    }


def write_output(
    outputs: dict[str, list[dict[str, Any]]], entities: list[dict[str, Any]], out_dir: Path
) -> None:
    """Write to a sibling temp dir, then swap it in, so readers never see a half-written run."""
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = out_dir.with_name(out_dir.name + ".tmp")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    for source, records in outputs.items():
        with (staging / f"{source}.jsonl").open("wb") as fh:
            for record in records:
                fh.write(orjson.dumps(record) + b"\n")
    (staging / "entities.json").write_bytes(
        orjson.dumps({"entities": entities}, option=orjson.OPT_INDENT_2)
    )
    previous = out_dir.with_name(out_dir.name + ".prev")
    shutil.rmtree(previous, ignore_errors=True)
    if out_dir.exists():
        out_dir.rename(previous)
    staging.rename(out_dir)
    shutil.rmtree(previous, ignore_errors=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description="Regenerate the anonymized output from a source")
    parser.add_argument("--source", required=True, help="dir:<path> | http | http:<base_url>")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--data-dir", default=None, help="registry and raw downloads (DATA_DIR)")
    args = parser.parse_args()
    settings = load_settings(args.data_dir)
    run_id = time.strftime("%Y%m%dT%H%M%S")
    source = make_source(args.source, settings, run_id)
    try:
        report = run(source, args.out, settings)
    finally:
        if isinstance(source, HttpSource):
            source.close()
            log.info("http requests: %d, raw saved under %s", source.requests, source.raw)
    log.info(
        "records: %s, persons: %d, seconds: %.1f", report.records, report.persons, report.seconds
    )
    log.info("missing ids vs record_index: %s", report.missing_ids)
    log.info("audit: %d records with leaked forms", len(report.audit_hits))
    for record_id, forms in list(report.audit_hits.items())[:20]:
        log.info("  leak %s: %s", record_id, sorted(set(forms))[:5])
    if report.pdf_xmp_docs:
        log.info("PDFs with XMP metadata: %s", report.pdf_xmp_docs)


if __name__ == "__main__":
    main()
