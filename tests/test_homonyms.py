"""Homonyms settled by evidence outside the name: a transaction code, a table column, the text
saying they are two persons, and the words around settled mentions in other sources."""

from kipu.anonymizer import Anonymizer
from kipu.detect.engine import column_role
from kipu.identity.homonyms import context_profiles, reference_keys
from kipu.ingest.records import DocRecord, TableRecord
from test_render import chat

TICKETS = """## Tickets de referencia

| Fecha | Agente | Cliente | Tema |
| --- | --- | --- | --- |
| 2026-09-03 | Luis Santander | Manuela Castaño | Pago reversado |
| 2026-08-12 | Kelly Castaño | Luis Santander | PSE no aplicado |
"""


def owners(anonymizer: Anonymizer, record_id: str, field: str, name: str) -> list[str | None]:
    return [m.person_id for m in anonymizer.spans[(record_id, field)] if m.text == name]


def doc(body: str) -> DocRecord:
    return DocRecord(id="doc_1", format="md", filename="d.md", title="", body=body, metadata={})


def cliente(anonymizer: Anonymizer, key: str) -> str | None:
    return anonymizer.directory.owner_of("cliente_id", key)


def test_column_role_reads_the_markdown_header() -> None:
    assert column_role(TICKETS, TICKETS.index("Luis Santander")) == "employee"
    assert column_role(TICKETS, TICKETS.rindex("Luis Santander")) == "client"
    assert column_role(TICKETS, TICKETS.index("Agente")) == ""
    assert column_role("Hablé con Luis Santander.", 10) == ""


def test_cliente_column_takes_the_client_homonym(anonymizer: Anonymizer) -> None:
    anonymizer.docs([doc(TICKETS)])
    agent, client = owners(anonymizer, "doc_1", "body", "Luis Santander")
    assert agent == anonymizer.directory.owner_of("kp_id", "KP-005")
    assert client == cliente(anonymizer, "CL-21823")


def test_transaction_code_settles_a_homonym_in_a_doc(anonymizer: Anonymizer) -> None:
    row = TableRecord(
        id="transacciones:TX-1",
        table="transacciones",
        columns={"cliente_id": "CL-57176", "descripcion": "Cuota inicial crédito S-2608-01"},
    )
    anonymizer.directory.references = reference_keys([row], anonymizer.directory)
    body = "| Solicitud | Nombre | Monto |\n|---|---|---|\n| S-2608-01 | Camila Arango | 45 M |"
    out = anonymizer.docs([doc(body)])
    assert owners(anonymizer, "doc_1", "body", "Camila Arango") == [cliente(anonymizer, "CL-57176")]
    assert "S-2608-01" in out[0]["body"]


def test_reference_keys_skip_record_keys_dates_and_shared_codes(anonymizer: Anonymizer) -> None:
    rows = [
        TableRecord(
            id=f"transacciones:TX-{i}",
            table="transacciones",
            columns={"cliente_id": key, "descripcion": text},
        )
        for i, (key, text) in enumerate(
            [
                ("CL-57176", "Pago PQR-2026-0001 del 2026-08-25, crédito 4471-0150"),
                ("CL-12538", "Abono 55-0192"),
                ("CL-88627", "Abono 55-0192"),
            ]
        )
    ]
    assert reference_keys(rows, anonymizer.directory) == {
        "4471-0150": cliente(anonymizer, "CL-57176")
    }


def test_two_mentions_said_to_be_distinct_go_to_both_homonyms(anonymizer: Anonymizer) -> None:
    text = (
        "Le respondieron la cédula 1022649507 de Camila Arango con el extracto de Camila "
        "Arango: son dos clientas distintas con el mismo nombre."
    )
    records = [chat("1", text, author="Kipi")]
    out = anonymizer.chats(records)
    first, second = owners(anonymizer, "1", "text", "Camila Arango")
    assert first == cliente(anonymizer, "CL-12538")
    assert second == cliente(anonymizer, "CL-57176")
    rendered = {
        anonymizer.renderer.short_name(anonymizer.directory.persons[p]) for p in (first, second)
    }
    assert all(name in out[0]["text"] for name in rendered)


def test_same_name_twice_without_a_cue_stays_one_person(anonymizer: Anonymizer) -> None:
    text = "Cédula 1022649507 de Camila Arango. Ya le escribí a Camila Arango."
    anonymizer.chats([chat("1", text, author="Kipi")])
    assert set(owners(anonymizer, "1", "text", "Camila Arango")) == {
        cliente(anonymizer, "CL-12538")
    }


def test_words_of_settled_mentions_elsewhere_beat_thread_context(anonymizer: Anonymizer) -> None:
    corpus = [
        "Aliada de contenido: Camila Arango, publica como carango@icloud.com; hará tres "
        "piezas sobre educación financiera con autorización firmada.",
        *(f"Ticket {i} sin relación" for i in range(20)),
    ]
    anonymizer.directory.profiles = context_profiles(corpus, anonymizer.directory)
    records = [
        chat("1", "Bienvenida al equipo, cédula 1022649507 registrada.", author="Kipi"),
        chat("2", "Camila Arango publicó la primera pieza de educación financiera.", author="Kipi"),
    ]
    anonymizer.chats(records)
    assert owners(anonymizer, "2", "text", "Camila Arango") == [cliente(anonymizer, "CL-57176")]


def test_without_profiles_thread_context_still_decides(anonymizer: Anonymizer) -> None:
    records = [
        chat("1", "Bienvenida al equipo, cédula 1022649507 registrada.", author="Kipi"),
        chat("2", "Camila Arango publicó la primera pieza de educación financiera.", author="Kipi"),
    ]
    anonymizer.chats(records)
    assert owners(anonymizer, "2", "text", "Camila Arango") == [cliente(anonymizer, "CL-12538")]
