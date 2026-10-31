"""Turn raw source payloads into normalized ChatRecord / DocRecord / TableRecord lists."""

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import pymupdf
from lxml import etree

from kipu.directory.names import split_name
from kipu.ingest.html_md import html_to_markdown
from kipu.ingest.records import Attachment, ChatRecord, DocRecord, TableRecord
from kipu.ingest.source import SourceClient, SourceNotFound

log = logging.getLogger(__name__)

MONTHS = {
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
}
TABLE_KEYS = {
    "clientes": "cliente_id",
    "transacciones": "tx_id",
    "pqrs": "radicado",
    "empleados": "id",
}
DOC_FORMATS = ("md", "html", "pdf")
CSV_FORMATS = {"clientes.csv": ("latin-1", ";"), "transacciones.csv": ("utf-8", ",")}
FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)


@dataclass
class RawCorpus:
    """Everything read from a source, plus the normalized records."""

    users: list[dict[str, Any]]
    channels: list[dict[str, Any]]
    messages: dict[str, list[dict[str, Any]]]
    tables_raw: dict[str, bytes]
    chats: list[ChatRecord] = field(default_factory=list)
    docs: list[DocRecord] = field(default_factory=list)
    tables: list[TableRecord] = field(default_factory=list)
    pdf_xmp: dict[str, str] = field(default_factory=dict)
    record_index: dict[str, list[str]] | None = None

    def records(self) -> list[ChatRecord | DocRecord | TableRecord]:
        return [*self.chats, *self.docs, *self.tables]


def load_corpus(source: SourceClient) -> RawCorpus:
    """Everything the listings enumerate. A listed date, doc or table that turns out missing
    (404) is skipped with a log line instead of failing the run."""
    users = source.users()
    channels = source.channels()
    messages = {ch["name"]: channel_messages(source, ch["name"]) for ch in channels}
    listing = source.tables_index()
    tables_raw = {}
    for entry in listing:
        try:
            tables_raw[entry["name"]] = source.table(entry["name"])
        except SourceNotFound:
            log.warning("table %s listed but not found, skipped", entry["name"])
    corpus = RawCorpus(users, channels, messages, tables_raw)
    corpus.chats = parse_chats(users, messages)
    corpus.docs, corpus.pdf_xmp = parse_docs(source)
    corpus.tables = parse_tables(tables_raw, {e["name"]: e for e in listing})
    try:
        corpus.record_index = source.record_index()
    except SourceNotFound:
        log.warning("record_index not found")
    return corpus


def channel_messages(source: SourceClient, channel: str) -> list[dict[str, Any]]:
    try:
        dates = source.channel_dates(channel)
    except SourceNotFound:
        log.warning("chats/%s: no dates listing, channel skipped", channel)
        return []
    messages = []
    for date in dates:
        try:
            day = source.messages(channel, date)
        except SourceNotFound:
            log.warning("chats/%s/%s: listed but not found, skipped", channel, date)
            continue
        if not day:
            log.info("chats/%s/%s: empty, skipped", channel, date)
        messages.extend(day)
    return messages


def author_name(user: dict[str, Any] | None) -> str:
    """Display name for a chat author: given names + first surname, as in the sample output."""
    if not user:
        return ""
    real = user.get("real_name") or user.get("profile", {}).get("real_name") or user.get("name", "")
    if user.get("is_bot"):
        return real
    return split_name(real).short if real else ""


def parse_chats(
    users: list[dict[str, Any]], messages: dict[str, list[dict[str, Any]]]
) -> list[ChatRecord]:
    by_uid = {u["id"]: u for u in users}
    records = []
    for channel, msgs in messages.items():
        for m in msgs:
            if not m.get("client_msg_id") or not m.get("ts"):
                log.warning("chats/%s: message without client_msg_id or ts, skipped", channel)
                continue
            user = by_uid.get(m.get("user", ""))
            author = author_name(user) or m.get("username", "")
            records.append(
                ChatRecord(
                    id=m["client_msg_id"],
                    thread_id=f"{channel}:{m.get('thread_ts') or m['ts']}",
                    author=author,
                    ts=m["ts"],
                    text=m.get("text", ""),
                    attachments=[
                        Attachment(filename=f.get("name", "")) for f in m.get("files", [])
                    ],
                )
            )
    return records


def humanize_title(filename: str) -> str:
    """`memo_cierre_julio.md` → `Memo cierre de julio`."""
    stem = filename.rsplit(".", 1)[0]
    words: list[str] = []
    for word in stem.replace("_", " ").split():
        if word.lower() in MONTHS and (not words or words[-1].lower() != "de"):
            words.append("de")
        words.append(word)
    text = " ".join(words)
    return text[:1].upper() + text[1:]


def parse_docs(source: SourceClient) -> tuple[list[DocRecord], dict[str, str]]:
    docs = []
    xmp: dict[str, str] = {}
    for entry in source.docs_index():
        if entry.get("format") not in DOC_FORMATS:
            log.warning("doc %s has unknown format %r, skipped", entry["id"], entry.get("format"))
            continue
        try:
            data = source.doc(entry["id"], entry["filename"])
        except SourceNotFound:
            log.warning("doc %s (%s) listed but not found, skipped", entry["id"], entry["filename"])
            continue
        fmt = entry["format"]
        if fmt == "md":
            body, metadata = parse_markdown(data.decode("utf-8"))
        elif fmt == "html":
            body, metadata = html_to_markdown(data.decode("utf-8"))
        else:
            body, metadata, xmp_text = parse_pdf(data)
            if xmp_text:
                xmp[entry["id"]] = xmp_text
        docs.append(
            DocRecord(
                id=entry["id"],
                format=fmt,
                filename=entry["filename"],
                title=humanize_title(entry["filename"]),
                body=body,
                metadata=metadata,
            )
        )
    return docs, xmp


def parse_markdown(text: str) -> tuple[str, dict[str, str]]:
    match = FRONT_MATTER.match(text)
    metadata: dict[str, str] = {}
    if match:
        for line in match.group(1).splitlines():
            key, sep, value = line.partition(":")
            if sep and key.strip() and not key.startswith((" ", "\t", "-")):
                metadata[key.strip()] = value.strip()
    return text, metadata


def parse_pdf(data: bytes) -> tuple[str, dict[str, str], str]:
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        body = "\n\n".join(page.get_text().strip() for page in doc) + "\n"
        metadata = {k: v for k, v in (doc.metadata or {}).items() if v}
        xmp_text = doc.get_xml_metadata() or ""
    return body, metadata, xmp_text


def parse_tables(
    tables_raw: dict[str, bytes], listing: dict[str, dict[str, Any]] | None = None
) -> list[TableRecord]:
    listing = listing or {}
    records = []
    for name, data in tables_raw.items():
        table = name.rsplit(".", 1)[0]
        if table not in TABLE_KEYS:
            log.warning("table %s is not in the output schema, skipped", name)
            continue
        if name.endswith(".csv"):
            rows = _csv_rows(data, *_csv_format(name, data, listing.get(name, {})))
        else:
            rows = _xml_rows(data)
        if not rows:
            continue
        key = TABLE_KEYS[table]
        for row in rows:
            records.append(TableRecord(id=f"{table}:{row[key]}", table=table, columns=row))
    return records


def _csv_format(name: str, data: bytes, meta: dict[str, Any]) -> tuple[str, str]:
    """Charset and separator: the API listing, then the known tables, then a guess."""
    known = CSV_FORMATS.get(name)
    encoding = meta.get("charset") or (known[0] if known else _guess_encoding(data))
    delimiter = meta.get("separator") or (known[1] if known else _guess_delimiter(data))
    return encoding, delimiter


def _guess_encoding(data: bytes) -> str:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return "latin-1"
    return "utf-8"


def _guess_delimiter(data: bytes) -> str:
    header = data.split(b"\n", 1)[0]
    return ";" if header.count(b";") > header.count(b",") else ","


def _csv_rows(data: bytes, encoding: str, delimiter: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(data.decode(encoding), newline=""), delimiter=delimiter)
    return [{k: v or "" for k, v in row.items()} for row in reader]


def _xml_rows(data: bytes) -> list[dict[str, str]]:
    root = etree.fromstring(data)
    rows = []
    for element in root:
        if not isinstance(element.tag, str):
            continue
        row = {k: v for k, v in element.attrib.items()}
        for child in element:
            if not isinstance(child.tag, str):
                continue
            row[child.tag] = child.text or ""
            for attr, value in child.attrib.items():
                row[f"{child.tag}_{attr}"] = value
        rows.append(row)
    return rows
