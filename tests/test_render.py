from conftest import SECRET
from kipu.anonymizer import Anonymizer
from kipu.ingest.records import Attachment, ChatRecord, TableRecord
from kipu.replace.render import fit_digits, format_date, spell_email


def chat(
    rid: str, text: str, author: str = "Manuela Castaño", files: tuple[str, ...] = ()
) -> ChatRecord:
    return ChatRecord(
        id=rid,
        thread_id="soporte:1",
        author=author,
        ts=f"1.{rid}",
        text=text,
        attachments=[Attachment(filename=f) for f in files],
    )


def test_helpers() -> None:
    assert fit_digits("1.107.360.039", "1107252808") == "1.107.252.808"
    assert fit_digits("+57 321 252 4068", "3001234567") == "+57 300 123 4567"
    assert format_date("1998-11-11", "dmy") == "11/11/1998"
    assert spell_email("szambrano@gmail.com", False) == "szambrano arroba gmail punto com"


def test_forms_are_preserved(anonymizer) -> None:
    records = [
        chat("1", "Hola, me llamo Manuela Castaño.", files=("extracto_manuela_castano_julio.pdf",)),
        chat("2", "EXTRACTO DE MANUELA CASTANO RUIZ"),
        chat("3", "Mi correo es mcastano arroba gmail punto com y la cédula 1.107.360.039"),
    ]
    out = {r["id"]: r for r in anonymizer.chats(records)}
    sur = anonymizer.factory.name(
        anonymizer.directory.persons[anonymizer.author_person("Manuela Castaño")]
    )
    assert out["1"]["text"] == f"Hola, me llamo {sur.short}."
    assert out["1"]["author"] == sur.short
    token = "_".join(sur.short.lower().split())
    assert out["1"]["attachments"][0]["filename"].startswith("extracto_")
    assert "manuela" not in out["1"]["attachments"][0]["filename"]
    upper = out["2"]["text"]
    assert upper.isupper() and upper.isascii() and "MANUELA" not in upper
    spelled = out["3"]["text"]
    assert " arroba " in spelled and " punto com" in spelled and "mcastano" not in spelled
    assert "1.107.360.039" not in spelled and len(spelled.split()[-1]) == len("1.107.360.039")
    assert token


def test_first_name_only_for_thread_participants(anonymizer) -> None:
    out = anonymizer.chats(
        [
            chat("1", "Me están cobrando dos veces."),
            chat("2", "Hola Manuela, soy Kelly de soporte.", author="Kelly Castaño"),
            chat("3", "Gracias Camila, ya quedó.", author="Kelly Castaño"),
        ]
    )
    assert "Manuela" not in out[1]["text"] and "Kelly" not in out[1]["text"]
    assert "Camila" in out[2]["text"]


def test_quasi_identifiers_are_generalized(anonymizer) -> None:
    out = anonymizer.chats([chat("1", "Yo soy fotógrafa, trabajo en Granada y entré en 2025.")])
    assert out[0]["text"] == (
        "Yo soy profesional de las industrias creativas, trabajo en Cali y entré en hace unos años."
    )


def test_kept_entities_untouched(anonymizer) -> None:
    out = anonymizer.chats([chat("1", "Pagué por PSE desde Bancolombia y Nequi.")])
    assert out[0]["text"] == "Pagué por PSE desde Bancolombia y Nequi."


def test_accents_follow_the_written_tokens(anonymizer) -> None:
    out = anonymizer.chats([chat("1", "Revisar a Camila Arango (CL-12538) hoy.", author="Kipi")])
    person = anonymizer.directory.persons[anonymizer.directory.owner_of("cliente_id", "CL-12538")]
    assert anonymizer.factory.name(person).short in out[0]["text"]


def test_reports_to_points_at_the_employee_homonym(anonymizer) -> None:
    row = TableRecord(
        id="empleados:KP-006",
        table="empleados",
        columns={"id": "KP-006", "nombre": "Clara Montoya Zapata", "reporta_a": "Luis Santander"},
    )
    out = anonymizer.tables([row])[0]["columns"]["reporta_a"]
    boss = anonymizer.directory.persons[anonymizer.directory.owner_of("kp_id", "KP-005")]
    assert out == anonymizer.factory.name(boss).short


def test_homonyms_split_by_nearby_client_id(registry, directory) -> None:
    directory.external_person("Camila Arango", "")
    anonymizer = Anonymizer(registry, directory, SECRET)
    out = anonymizer.chats(
        [
            chat("1", "Revisar a Camila Arango (CL-12538) hoy.", author="Kipi"),
            chat("2", "Revisar a Camila Arango (CL-57176) hoy.", author="Kipi"),
        ]
    )
    for record, key in zip(out, ("CL-12538", "CL-57176"), strict=True):
        person = directory.persons[directory.owner_of("cliente_id", key)]
        assert anonymizer.factory.name(person).short in record["text"]
