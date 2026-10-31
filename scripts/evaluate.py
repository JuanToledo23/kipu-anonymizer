"""Local evaluator: runs validar.mjs, the given test files, and an optional field diff.

Usage:
  uv run python scripts/evaluate.py <output_dir> --public <dir with record_index.json>
      [--tests tests.json ...] [--expected <expected_output dir>]
"""

import argparse
import difflib
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from kipu.audit.auditor import flatten
from kipu.audit.texto import contiene, norm
from kipu.directory.names import split_name, strip_accents

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ("chats", "docs", "tables")


def load_output(folder: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    records: dict[str, dict[str, Any]] = {}
    for source in SOURCES:
        path = folder / f"{source}.jsonl"
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    record = json.loads(line)
                    records[record["id"]] = record
    entities_path = folder / "entities.json"
    entities = (
        json.loads(entities_path.read_text(encoding="utf-8"))["entities"]
        if entities_path.is_file()
        else []
    )
    return records, entities


def field_text(record: dict[str, Any], field: str | None) -> str:
    """Port of `textoDeRegistro` in validar.mjs."""
    if field in (None, "", "any", "raw", "*"):
        return " | ".join(flatten({k: v for k, v in record.items() if k != "id"}))
    if field.startswith("columns."):
        column = field.split(".", 1)[1]
        columns = record.get("columns") or {}
        return " | ".join(flatten(columns if column == "*" else columns.get(column)))
    return " | ".join(flatten(record.get(field)))


class Evaluator:
    def __init__(self, records: dict[str, dict[str, Any]], entities: list[dict[str, Any]]) -> None:
        self.records = records
        self.entities = [e for e in entities if not e.get("kept")]
        self.whole = "\n".join(field_text(r, None) for r in records.values())

    def run(self, test: dict[str, Any]) -> tuple[bool, str]:
        handler = getattr(self, f"_{test['family']}", None)
        if handler is None:
            return False, f"unknown family {test['family']}"
        return handler(test)

    def _locator(self, test: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        loc = test.get("locator") or {}
        record = self.records.get(loc.get("record") or "")
        return record, field_text(record, loc.get("field")) if record else ""

    def _directa(self, test: dict[str, Any]) -> tuple[bool, str]:
        record, text = self._locator(test)
        if record is None:
            return False, "locator record missing from output"
        for payload in test["payload"]:
            if contiene(text, payload):
                return False, "payload still in the located field"
            if contiene(self.whole, payload):
                hits = [
                    rid for rid, r in self.records.items() if contiene(field_text(r, None), payload)
                ]
                return False, f"payload elsewhere in output: {hits[:3]}"
        return True, "payload gone"

    def _oculta(self, test: dict[str, Any]) -> tuple[bool, str]:
        record, text = self._locator(test)
        if record is None:
            return False, "locator record missing from output"
        leaked = [p for p in test["payload"] if contiene(text, p)]
        return (False, "payload still in the located field") if leaked else (True, "payload gone")

    def _falso_positivo(self, test: dict[str, Any]) -> tuple[bool, str]:
        record, text = self._locator(test)
        if record is None:
            return False, "locator record missing from output"
        missing = [p for p in test["payload"] if p not in text]
        return (False, f"kept term altered: {missing}") if missing else (True, "kept intact")

    def _indirecta(self, test: dict[str, Any]) -> tuple[bool, str]:
        record, text = self._locator(test)
        if record is None:
            return False, "locator record missing from output"
        joined = [text] + [
            field_text(self.records[r], None)
            for r in test.get("join_records", [])
            if r in self.records
        ]
        union = "\n".join(joined)
        present = [p for p in test["payload"] if contiene(union, p)]
        if len(present) == len(test["payload"]):
            return False, "all quasi-identifier components still co-occur"
        return True, f"broken ({len(present)}/{len(test['payload'])} components remain)"

    def _entities_with(self, forms: list[str]) -> list[dict[str, Any]]:
        wanted = {norm(f) for f in forms}
        return [e for e in self.entities if wanted & {norm(o) for o in e.get("originals", [])}]

    def _consistencia(self, test: dict[str, Any]) -> tuple[bool, str]:
        matches = self._entities_with(test.get("lookup_forms", []))
        if len(matches) != 1:
            return False, f"lookup forms map to {len(matches)} entities"
        entity = matches[0]
        name = (entity.get("pseudonyms") or {}).get("name")
        if not name:
            return False, "entity has no name pseudonym"
        records = test.get("records") or entity.get("records", [])
        forms = pseudonym_forms(name)
        missing = [
            r
            for r in records
            if r in self.records
            and not any(contiene(field_text(self.records[r], None), f) for f in forms)
        ]
        if missing:
            return (
                False,
                f"pseudonym '{name}' absent from {len(missing)} records, e.g. {missing[:2]}",
            )
        unknown = [r for r in records if r not in self.records]
        return True, f"one entity, pseudonym in all {len(records) - len(unknown)} records"

    def _homonimos(self, test: dict[str, Any]) -> tuple[bool, str]:
        payload = test["payload"][0]
        short = norm(split_name(payload).short)
        owners = self._entities_with([payload])
        if not owners:
            return False, "no entity declares the payload"
        group = [
            e
            for e in self.entities
            if any(norm(split_name(o).short) == short for o in e.get("originals", []) if " " in o)
        ]
        names = {(e.get("pseudonyms") or {}).get("name") for e in group}
        if len(group) < 2:
            return False, f"only {len(group)} entity for the homonym name"
        if len(names) < len(group):
            return False, "homonyms share a pseudonym"
        return True, f"{len(group)} homonyms with distinct pseudonyms"


def pseudonym_forms(name: str) -> list[str]:
    """The pseudonym as it can legitimately appear: full, given names only, filename token."""
    parts = split_name(name)
    token = "_".join(strip_accents(name).lower().split())
    return [name, parts.first, token]


def run_validator(output: Path, public: Path) -> bool:
    proc = subprocess.run(
        ["node", str(ROOT / "validar.mjs"), str(output), "--public", str(public)],
        capture_output=True,
        text=True,
    )
    print(proc.stdout.strip())
    return proc.returncode == 0


def diff_expected(records: dict[str, dict[str, Any]], expected_dir: Path, show: int = 12) -> None:
    expected, _ = load_output(expected_dir)
    counts: Counter[str] = Counter()
    samples: list[str] = []
    for rid, exp in expected.items():
        got = records.get(rid)
        if got is None:
            counts["missing_record"] += 1
            continue
        for key, exp_value in _fields(exp).items():
            got_value = _fields(got).get(key)
            if got_value == exp_value:
                counts["same"] += 1
                continue
            counts["different"] += 1
            if len(samples) < show:
                samples.append(_word_diff(rid, key, str(exp_value), str(got_value)))
    print(f"field diff vs expected: {dict(counts)}")
    for sample in samples:
        print(sample)


def _fields(record: dict[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in record.items():
        if isinstance(value, dict):
            flat.update({f"{key}.{k}": v for k, v in value.items()})
        else:
            flat[key] = value
    return flat


def _word_diff(rid: str, key: str, expected: str, got: str) -> str:
    matcher = difflib.SequenceMatcher(a=expected.split(), b=got.split())
    changes = [
        f"[{' '.join(expected.split()[i1:i2])} → {' '.join(got.split()[j1:j2])}]"
        for op, i1, i2, j1, j2 in matcher.get_opcodes()
        if op != "equal"
    ]
    return f"  {rid} {key}: {' '.join(changes)[:300]}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--public", type=Path, required=True)
    parser.add_argument("--tests", type=Path, action="append", default=[])
    parser.add_argument("--expected", type=Path)
    args = parser.parse_args()

    print("== validator ==")
    ok = run_validator(args.output, args.public)
    records, entities = load_output(args.output)
    evaluator = Evaluator(records, entities)
    totals: dict[str, Counter[str]] = defaultdict(Counter)
    print("== tests ==")
    for tests_file in args.tests:
        for test in json.loads(tests_file.read_text(encoding="utf-8")):
            passed, reason = evaluator.run(test)
            totals[test["family"]]["pass" if passed else "fail"] += 1
            ok = ok and passed
            verdict = "PASS" if passed else "FAIL"
            mechanism = test.get("mechanism", "")
            print(f"{verdict} {test['id']:<10} {test['family']:<15} {mechanism}: {reason}")
    print("== totals by family ==")
    for family, counter in sorted(totals.items()):
        print(f"{family:<15} {counter['pass']}/{counter['pass'] + counter['fail']}")
    if args.expected:
        print("== diff vs expected ==")
        diff_expected(records, args.expected)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
