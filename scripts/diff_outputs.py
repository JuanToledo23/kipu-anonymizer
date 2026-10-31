"""Diff of two output dirs built from the same raw corpus (e.g. rules only vs LLM layer).

Every difference must be a newly replaced span: a region that was still original text in the
base output (it occurs in the raw field) and is replaced by non-empty text in the new one.
Anything else (insertions, deletions, a change to text the base had already replaced, a
missing record or field) is printed as a BUG and the script exits 1.

Usage:
  uv run python scripts/diff_outputs.py <base_out> <new_out> --raw <data/raw/<run>>
"""

import argparse
import difflib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from kipu.ingest.parsers import load_corpus
from kipu.ingest.source import DirSource

SOURCES = ("chats", "docs", "tables")
TOKEN = re.compile(r"\w+|\s+|[^\w\s]")


def load(folder: Path) -> dict[str, dict[str, Any]]:
    records = {}
    for source in SOURCES:
        for line in (folder / f"{source}.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                records[record["id"]] = record
    return records


def paths(value: Any, prefix: str = "") -> dict[str, str]:
    if isinstance(value, dict):
        return {p: v for k, item in value.items() for p, v in paths(item, f"{prefix}{k}.").items()}
    if isinstance(value, list):
        return {
            p: v for i, item in enumerate(value) for p, v in paths(item, f"{prefix}{i}.").items()
        }
    return {prefix.rstrip("."): str(value)}


def regions(old: str, new: str) -> list[tuple[str, str]]:
    a, b = TOKEN.findall(old), TOKEN.findall(new)
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    found, current = [], None
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal" and not (i2 - i1 == 1 and a[i1].isspace() and current):
            if current:
                found.append(current)
                current = None
            continue
        piece = ("".join(a[i1:i2]), "".join(b[j1:j2]))
        current = (current[0] + piece[0], current[1] + piece[1]) if current else piece
    if current:
        found.append(current)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base", type=Path)
    parser.add_argument("new", type=Path)
    parser.add_argument("--raw", type=Path, required=True)
    args = parser.parse_args()
    corpus = load_corpus(DirSource(args.raw))
    raw = {r.id: paths(r.model_dump()) for r in corpus.records()}
    base, new = load(args.base), load(args.new)
    bugs: list[str] = []
    changed_records, regions_ok = 0, Counter()
    for record_id in sorted(set(base) | set(new)):
        if record_id not in base or record_id not in new:
            bugs.append(f"{record_id}: present in only one output")
            continue
        old_fields, new_fields = paths(base[record_id]), paths(new[record_id])
        if set(old_fields) != set(new_fields):
            bugs.append(f"{record_id}: field set differs")
            continue
        changed = False
        for field, old in old_fields.items():
            if old == new_fields[field]:
                continue
            changed = True
            source = raw.get(record_id, {}).get(field, "")
            for before, after in regions(old, new_fields[field]):
                if not before.strip() or not after.strip():
                    # Lengths only: the text may be an original value.
                    bugs.append(
                        f"{record_id} {field}: insertion or deletion ({len(before)}→{len(after)})"
                    )
                elif before.strip() not in source:
                    bugs.append(f"{record_id} {field}: changed text the base had replaced")
                else:
                    regions_ok[field.split(".")[0]] += 1
        changed_records += changed
    print(f"changed records: {changed_records}; new replaced regions by field: {dict(regions_ok)}")
    for bug in bugs:
        print("BUG", bug)
    print(f"BUGS: {len(bugs)}")
    return 1 if bugs else 0


if __name__ == "__main__":
    sys.exit(main())
