"""Normalized record shapes shared by the pipeline, output files and the HTTP API."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class Attachment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str


class ChatRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    thread_id: str
    author: str
    ts: str
    text: str
    attachments: list[Attachment]


class DocRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    format: Literal["pdf", "html", "md"]
    filename: str
    title: str
    body: str
    metadata: dict[str, str]


class TableRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    table: Literal["clientes", "transacciones", "pqrs", "empleados"]
    columns: dict[str, str]


Record = ChatRecord | DocRecord | TableRecord
SourceName = Literal["chats", "docs", "tables"]
