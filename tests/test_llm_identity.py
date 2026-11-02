"""Identity of model-found persons, the full batch flow, omissions and generalization."""

from pathlib import Path
from typing import Any

from tests.llm_fakes import LLM, PRIMARY, SECOND, STRONG, FakeProviders, client, extraction

from conftest import SECRET
from kipu.anonymizer import Anonymizer
from kipu.detect.engine import Group, Surface
from kipu.directory.directory import Directory
from kipu.directory.registry import Registry
from kipu.ingest.records import ChatRecord
from kipu.llm.generalize import acceptable, same_leading_case, table_phrase, table_sector
from kipu.llm.identity import LLMMentions, checked_verdict
from kipu.llm.layer import LLMLayer
from kipu.llm.omissions import audit_spans
from kipu.llm.verify import Span


def hook(anonymizer: Anonymizer, judge: Any = None) -> LLMMentions:
    return LLMMentions({}, anonymizer.directory, anonymizer.index, set(), {}, judge)


def person(directory: Directory, id_type: str, value: str) -> str:
    return directory.owner_of(id_type, value)


def test_given_name_linking(anonymizer: Anonymizer, directory: Directory) -> None:
    kelly = person(directory, "kp_id", "KP-016")
    employee_luis = person(directory, "kp_id", "KP-005")
    h = hook(anonymizer)
    thread = Group([], scope="t1")
    span = Span("m", "text", 0, 5, "kelly", "PERSON")
    assert h._person(span, thread, [kelly]) == (kelly, "llm_given")
    luis = Span("m", "text", 0, 4, "Luis", "PERSON")
    assert h._person(luis, thread, []) == (employee_luis, "llm_given")
    stranger = Span("m", "text", 0, 8, "Yeferson", "PERSON", gender="m")
    first, kind = h._person(stranger, thread, [])
    assert kind == "llm_given" and first not in (kelly, employee_luis)
    assert directory.persons[first].gender == "M" and first in h.created
    assert h._person(stranger, thread, [])[0] == first
    assert h._person(stranger, Group([], scope="t2"), [])[0] != first


def test_homonyms_need_cited_evidence(anonymizer: Anonymizer, directory: Directory) -> None:
    a = person(directory, "cliente_id", "CL-12538")
    b = person(directory, "cliente_id", "CL-57176")
    text = "Reclamo de Camila Osorio. Al final del caso: ver CL-12538 para el soporte."
    group = Group([Surface("m", "text", text)], scope="t9")
    span = Span("m", "text", 11, 24, "Camila Osorio", "PERSON")
    asked: list[dict] = []

    def judge(name: str, candidates: list[str], txt: str, evidence: dict) -> str | None:
        asked.append(evidence)
        return checked_verdict(
            {"person_id": a, "evidence": "ver CL-12538"}, candidates, txt, evidence
        )

    assert hook(anonymizer, judge)._person(span, group, []) == (a, "llm_full")
    assert asked == [{a: ["CL-12538"], b: []}]
    lying = {"person_id": b, "evidence": "ver CL-12538"}
    assert checked_verdict(lying, [a, b], text, {a: ["CL-12538"], b: []}) is None
    no_evidence = Group([Surface("m", "text", "Camila Osorio escribió")], scope="t9")
    separate, _ = hook(anonymizer, judge)._person(span, no_evidence, [])
    assert separate not in (a, b) and len(asked) == 1


THREAD = [
    ChatRecord(
        id="m1",
        thread_id="t1",
        author="Manuela Cardeño",
        ts="1",
        text="Hola, el giro lo hizo mi cuñado Yeferson Mosquera, él tiene 52 años.",
        attachments=[],
    ),
    ChatRecord(
        id="m2",
        thread_id="t1",
        author="Kelly Cardeño",
        ts="2",
        text="Gracias. ¿Yeferson Mosquera tiene cuenta en Kipu Pay?",
        attachments=[],
    ),
]


def test_third_party_becomes_a_new_person(
    registry: Registry, directory: Directory, tmp_path: Path
) -> None:
    def run_pass(factory: Any, sectors: dict[str, str]) -> tuple[Anonymizer, dict]:
        anonymizer = Anonymizer(registry, directory, SECRET, sectors=sectors)
        if factory:
            anonymizer.detector.extra = factory(anonymizer)
        return anonymizer, {"chats": anonymizer.chats(THREAD), "docs": [], "tables": []}

    rules = run_pass(None, {})[1]["chats"]
    assert "Yeferson Mosquera" in rules[0]["text"]
    fake = FakeProviders(
        {PRIMARY: extraction, SECOND: extraction, STRONG: lambda u: {"verdicts": []}}
    )
    layer = LLMLayer(LLM, tmp_path, client(tmp_path, fake))
    anonymizer, outputs = layer.refine_corpus(THREAD, [], [], run_pass)
    out = outputs["chats"]
    assert all("Yeferson" not in r["text"] for r in out)
    assert "entre 50 y 59 años" in out[0]["text"]
    assert out[0]["text"].startswith("Hola, el giro lo hizo mi cuñado ")
    assert out[1]["text"].startswith("Gracias. ¿") and out[1]["text"].endswith(
        " tiene cuenta en Kipu Pay?"
    )
    entities = anonymizer.entities(out)
    third = [e for e in entities if "Yeferson Mosquera" in e.get("originals", [])]
    assert len(third) == 1 and third[0]["records"] == ["m1", "m2"]
    assert directory.persons[third[0]["entity_id"]].gender == "M"
    assert layer.report.persons_created == 1
    assert layer.report.mentions == {("NAME", "both"): 2, ("QUASI_AGE", "both"): 1}
    assert layer.report.omissions == [0]


def test_leaked_forms_are_replaced_where_they_leak() -> None:
    entities = [{"entity_id": "p_9", "originals": ["5832-0274", "Yeferson Mosquera"]}]
    records = [{"id": "b", "text": "crédito 5832-0274 de Yeferson Mosquera"}]
    raw = {("b", "text"): "crédito 5832-0274 de Yeferson Mosquera"}
    spans = audit_spans(records, entities, raw, {}, lambda form: form == "5832-0274")
    assert [(s.start, s.end, s.label, s.person_id) for s in spans] == [
        (8, 17, "ACCOUNT", "p_9"),
        (21, 38, "PERSON", "p_9"),
    ]


def test_generalized_phrases_must_be_replacements_not_rewrites() -> None:
    assert acceptable("soy odontóloga en Laureles", "soy profesional de la salud en Medellín")
    assert acceptable("cliente desde 2019", "cliente desde hace unos años")
    assert acceptable("Odontóloga", same_leading_case("Odontóloga", "profesional de la salud"))
    assert not acceptable("gerente de riesgo", "Trabaja como gerente de riesgo.")
    assert not acceptable("arquitecta", "arquitecto")
    assert not acceptable("chef", "chef")
    assert not acceptable("vive en Laureles", "vive en Laureles, una ciudad")
    assert not acceptable("cliente desde 2019", "No puedo generalizar la frase de 2019")


def test_occupations_use_the_table_first() -> None:
    assert table_sector("diseñadora") == "profesional de las industrias creativas"
    assert table_sector("Odontóloga") == "profesional de la salud"
    assert table_sector("gerente de riesgo") is None
    assert table_phrase("soy diseñadora") == "soy profesional de las industrias creativas"
    assert table_phrase("Ingeniera de sistemas en Cali") is None  # a place remains: model
    assert table_phrase("es enfermera desde 2019") is None
