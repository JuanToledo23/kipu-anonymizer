import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import SECRET
from kipu.anonymizer import Anonymizer
from kipu.api import create_app
from kipu.config import Settings
from kipu.ingest.source import DirSource
from kipu.pipeline import run
from test_render import chat

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample"


def test_declared_name_is_the_canonical_short_form(registry, directory) -> None:
    for live in (False, True):
        anonymizer = Anonymizer(registry, directory, SECRET, live=live)
        out = anonymizer.chats([chat("1", "Soy Manuela Cardeño Ruiz", author="Kipi")])
        person = anonymizer.directory.persons[anonymizer.directory.owner_of("cedula", "1108470149")]
        entity = next(e for e in anonymizer.entities(out) if e["entity_id"] == person.person_id)
        assert entity["pseudonyms"]["name"] == anonymizer.factory.name(person).short


@pytest.mark.skipif(not (SAMPLE / "input").is_dir(), reason="sample data not downloaded")
def test_anonymize_declares_the_same_name_as_entities_json(tmp_path: Path) -> None:
    settings = Settings(tmp_path, "secret", "", "", "", 9000)
    run(DirSource(SAMPLE / "input"), settings.output_dir, settings)
    published = json.loads((settings.output_dir / "entities.json").read_text("utf-8"))["entities"]
    persons = [e for e in published if e["type"] == "person" and "name" in e["pseudonyms"]]
    owners: dict[str, list[str]] = {}
    for e in persons:
        for form in e["originals"]:
            owners.setdefault(form, []).append(e["entity_id"])
    entity, full = next(
        (e, form)
        for e in persons
        for form in e["originals"]
        if re.fullmatch(r"[^\W\d_]+(?: [^\W\d_]+){2,3}", form) and owners[form] == [e["entity_id"]]
    )
    record = {
        "id": "m1",
        "thread_id": "c:1",
        "author": "Kipi",
        "ts": "1",
        "text": f"Hola, soy {full}",
        "attachments": [],
    }
    client = TestClient(create_app(settings))
    body = client.post("/v1/anonymize", json={"source": "chats", "records": [record]}).json()
    live = next(e for e in body["entities"] if e["entity_id"] == entity["entity_id"])
    assert live["pseudonyms"]["name"] == entity["pseudonyms"]["name"]
