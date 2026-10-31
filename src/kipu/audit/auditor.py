"""Leak auditor: searches the serialized output for every original form, like the judge does."""

from collections import defaultdict
from typing import Any

import regex

from kipu.audit.texto import contiene

RX_DIGIT = regex.compile(r"\p{Nd}")


def flatten(value: Any) -> list[str]:
    """Mirror of `plano` in validar.mjs: keys and values, recursively, as strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, bool):
        return ["True" if value else "False"]
    if isinstance(value, int | float):
        return [str(value)]
    if isinstance(value, list):
        return [s for v in value for s in flatten(v)]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in (k, *flatten(v))]
    return [str(value)]


def record_text(record: dict[str, Any]) -> str:
    return " | ".join(flatten({k: v for k, v in record.items() if k != "id"}))


def is_bare_name(form: str) -> bool:
    """The judge skips single capitalized words; so do we, to report what it would report."""
    first = form[:1]
    return (
        " " not in form
        and "@" not in form
        and not RX_DIGIT.search(form)
        and first != ""
        and first.upper() == first
        and first.lower() != first
    )


def audit(records: list[dict[str, Any]], entities: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Return {record_id: [leaked forms]} for forms of non-kept entities still in the output."""
    texts = [record_text(r) for r in records]
    whole = "\n".join(texts)
    kept = [
        o
        for e in entities
        if e.get("kept")
        for o in e.get("originals", [])
        if len(o) >= 4 and regex.search(r"\p{L}", o)
    ]
    hits: dict[str, list[str]] = defaultdict(list)
    for entity in entities:
        if entity.get("kept"):
            continue
        for form in entity.get("originals", []):
            if len(form) < 3 or is_bare_name(form) or any(contiene(k, form) for k in kept):
                continue
            if not contiene(whole, form):
                continue
            for record, text in zip(records, texts, strict=True):
                if contiene(text, form):
                    hits[record["id"]].append(form)
    return dict(hits)
