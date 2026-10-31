"""Págalo migration: the grown corpus against a copy of the registry of official run 1.

Duplicate client rows join the old person, renamed old users keep theirs while their new
homonyms get their own, nobody is created without an identifier, managers are found by
name, and the new docs' filename and PDF author are replaced. Rules only (no LLM).
"""

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from kipu.audit.texto import contiene
from kipu.config import Settings
from kipu.directory.names import fold
from kipu.directory.registry import Registry
from kipu.directory.seed import norm_identifier
from kipu.ingest.parsers import load_corpus
from kipu.ingest.source import DirSource
from kipu.pipeline import OUTPUT_FILES, RunReport, run

ROOT = Path(__file__).resolve().parent.parent
GROWN = ROOT / "data" / "raw" / "growth_20261031T215550"
BASELINE = ROOT / "data" / "baseline_run1"

pytestmark = pytest.mark.skipif(
    not (GROWN / "record_index.json").is_file() or not (BASELINE / "registry.sqlite").is_file(),
    reason="grown snapshot or run-1 registry not under data/",
)

# (old cliente_id, Págalo cliente_id): one person, listed by both portfolios.
DUPLICATES = [
    ("CL-15468", "PG-2274"),
    ("CL-19162", "PG-5162"),
    ("CL-19676", "PG-5470"),
    ("CL-23528", "PG-4466"),
    ("CL-30787", "PG-3414"),
    ("CL-39098", "PG-9222"),
    ("CL-55931", "PG-3085"),
    ("CL-71712", "PG-7505"),
    ("CL-74990", "PG-5817"),
    ("CL-78641", "PG-2690"),
    ("CL-93152", "PG-3773"),
    ("CL-98782", "PG-3809"),
]
# Old user whose real_name was shortened → the new person who now has that name.
RENAMED = {
    "U0GPUPL6M": ("kp_id", "KP-192"),
    "U0YN91VJN": ("cliente_id", "PG-9192"),
    "U0YDE69RK": ("cliente_id", "PG-9638"),
}
# Chat record naming one of those names bare → (renamed uid, True if it is the new person).
# A participant of the thread wins; else the person whose identifiers are in the thread.
BARE_MENTIONS = {
    "f9679dfe-a54c-5542-b942-262530651871": ("U0GPUPL6M", True),
    "120e5fb6-70d6-5115-ae0e-9fd210d1c351": ("U0GPUPL6M", True),
    "096d1e40-f04e-5e4d-a540-d1ace31e1841": ("U0YN91VJN", False),
    "7f546f7c-df00-5e83-bb69-f67fa72dd50f": ("U0YN91VJN", True),
    "0689bd77-2ca1-5deb-b3d9-2b92c98e1b0a": ("U0YDE69RK", False),
    "bd25b88a-3c38-52e0-8bf6-6b856076e88b": ("U0YDE69RK", True),
}
NEW_EMPLOYEES = [f"KP-{n}" for n in range(191, 197)]
ROW_DIFFERENCES = {"cliente_id", "fecha_vinculacion", "notas"}


@dataclass
class Runs:
    first: RunReport
    second: RunReport
    out1: Path
    out2: Path
    registry: Path
    persons: tuple[int, int]


def person_count(path: Path) -> int:
    registry = Registry(path)
    try:
        return registry.conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
    finally:
        registry.close()


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> Runs:
    tmp = tmp_path_factory.mktemp("pagalo")
    data = tmp / "data"
    data.mkdir()
    shutil.copy(BASELINE / "registry.sqlite", data / "registry.sqlite")
    settings = Settings(data, "secret", "", "", "", 9000)
    first = run(DirSource(GROWN), tmp / "out1", settings)
    after_first = person_count(settings.registry_path)
    second = run(DirSource(GROWN), tmp / "out2", settings)
    return Runs(
        first,
        second,
        tmp / "out1",
        tmp / "out2",
        settings.registry_path,
        (after_first, person_count(settings.registry_path)),
    )


def owner(path: Path, id_type: str, raw: str) -> str | None:
    registry = Registry(path)
    try:
        return registry.owner(id_type, norm_identifier(id_type, raw))
    finally:
        registry.close()


def entities(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {e["entity_id"]: e for e in data["entities"]}


def records(out: Path, source: str) -> dict[str, dict[str, Any]]:
    lines = (out / f"{source}.jsonl").read_text(encoding="utf-8").splitlines()
    return {r["id"]: r for r in map(json.loads, lines)}


def test_duplicate_client_rows_are_the_old_person(runs: Runs) -> None:
    baseline = entities(BASELINE / "entities.json")
    after = entities(runs.out1 / "entities.json")
    rows = records(runs.out1, "tables")
    for old_id, new_id in DUPLICATES:
        person = owner(BASELINE / "registry.sqlite", "cliente_id", old_id)
        assert owner(runs.registry, "cliente_id", old_id) == person
        assert owner(runs.registry, "cliente_id", new_id) == person
        assert after[person]["pseudonyms"]["name"] == baseline[person]["pseudonyms"]["name"]
        assert f"clientes:{new_id}" in after[person]["records"]
        old_row = rows[f"clientes:{old_id}"]["columns"]
        new_row = rows[f"clientes:{new_id}"]["columns"]
        assert {k for k in old_row if old_row[k] != new_row[k]} <= ROW_DIFFERENCES


def test_renamed_users_keep_their_person_and_homonyms_get_their_own(runs: Runs) -> None:
    baseline = entities(BASELINE / "entities.json")
    after = entities(runs.out1 / "entities.json")
    for uid, (id_type, value) in RENAMED.items():
        old = owner(BASELINE / "registry.sqlite", "uid", uid)
        new = owner(runs.registry, id_type, value)
        assert owner(runs.registry, "uid", uid) == old
        assert new and new not in baseline and new != old
        assert after[new]["pseudonyms"]["name"] != after[old]["pseudonyms"]["name"]
    for record_id, (uid, is_new) in BARE_MENTIONS.items():
        old = owner(runs.registry, "uid", uid)
        new = owner(runs.registry, *RENAMED[uid])
        expected, other = (new, old) if is_new else (old, new)
        assert record_id in after[expected]["records"]
        assert record_id not in after[other]["records"]


def test_rerun_creates_nobody_and_is_byte_identical(runs: Runs) -> None:
    assert runs.first.persons_created == 48  # 42 new Págalo clients and 6 KP-19x employees
    assert runs.second.persons_created == 0
    assert runs.persons[0] == runs.persons[1]
    for name in OUTPUT_FILES:
        assert (runs.out1 / name).read_bytes() == (runs.out2 / name).read_bytes(), name
    registry = Registry(runs.registry)
    try:
        bare = registry.conn.execute(
            "SELECT COUNT(*) FROM persons p WHERE NOT EXISTS "
            "(SELECT 1 FROM identifiers i WHERE i.person_id = p.person_id)"
        ).fetchone()[0]
    finally:
        registry.close()
    assert bare == 0


def test_new_employees_report_to_the_old_managers_pseudonym(runs: Runs) -> None:
    corpus = load_corpus(DirSource(GROWN))
    staff = {r.columns["id"]: r.columns for r in corpus.tables if r.table == "empleados"}
    after = entities(runs.out1 / "entities.json")
    rows = records(runs.out1, "tables")
    for kp in NEW_EMPLOYEES:
        named = fold(staff[kp]["reporta_a"]).split()
        managers = [k for k, c in staff.items() if fold(c["nombre"]).split()[:1] == named[:1]]
        managers = [k for k in managers if named[-1] in fold(staff[k]["nombre"]).split()]
        assert len(managers) == 1, kp
        manager = owner(runs.registry, "kp_id", managers[0])
        written = rows[f"empleados:{kp}"]["columns"]["reporta_a"]
        assert set(written.split()) <= set(after[manager]["pseudonyms"]["name"].split())
        assert f"empleados:{kp}" in after[manager]["records"]


def test_new_docs_filename_and_pdf_author_are_replaced(runs: Runs) -> None:
    raw = {d.id: d for d in load_corpus(DirSource(GROWN)).docs}
    out = records(runs.out1, "docs")
    person = raw["doc_28"].filename.removeprefix("notice_assignment_").removesuffix(".pdf")
    assert out["doc_28"]["filename"].startswith("notice_assignment_")
    for form in (person, person.replace("_", " ")):
        assert not contiene(out["doc_28"]["filename"], form)
    for doc_id in ("doc_28", "doc_32", "doc_33"):
        author = raw[doc_id].metadata["author"]
        assert out[doc_id]["metadata"]["author"]
        assert not contiene(out[doc_id]["metadata"]["author"], author), doc_id
