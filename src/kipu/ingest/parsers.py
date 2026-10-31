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
from kipu.ingest.source import SourceClient

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
    users = source.users()
    channels = source.channels()
    messages = {
        ch["name"]: [
            m for d in source.channel_dates(ch["name"]) for m in source.messages(ch["name"], d)
        ]
        for ch in channels
    }
    tables_raw = {name: source.table(name) for name in source.table_names()}
    corpus = RawCorpus(users, channels, messages, tables_raw)
    corpus.chats = parse_chats(users, messages)
    corpus.docs, corpus.pdf_xmp = parse_docs(source)
    corpus.tables = parse_tables(tables_raw)
    corpus.record_index = source.record_index()
    return corpus


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
        data = source.doc(entry["id"], entry["filename"])
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


def parse_tables(tables_raw: dict[str, bytes]) -> list[TableRecord]:
    records = []
    for name, data in tables_raw.items():
        table = name.rsplit(".", 1)[0]
        rows = _csv_rows(name, data) if name.endswith(".csv") else _xml_rows(data)
        if not rows:
            continue
        key = TABLE_KEYS.get(table) or next(iter(rows[0]))
        for row in rows:
            records.append(TableRecord(id=f"{table}:{row[key]}", table=table, columns=row))
    return records


def _csv_rows(name: str, data: bytes) -> list[dict[str, str]]:
    encoding, delimiter = CSV_FORMATS.get(name, ("utf-8", ","))
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
