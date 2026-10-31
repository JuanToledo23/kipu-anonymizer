from pathlib import Path

from conftest import SECRET
from kipu.anonymizer import Anonymizer
from kipu.audit.texto import contiene
from kipu.directory.directory import Directory
from kipu.directory.registry import Registry
from kipu.directory.seed import seeds_from_users
from test_render import chat

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample"


def given_of(anonymizer: Anonymizer, author: str) -> str:
    """What a one-token given name of a single-given-name person renders to."""
    person = anonymizer.directory.persons[anonymizer.author_person(author)]
    return " ".join(anonymizer.factory.name(person).given)


def test_lowercase_given_name_of_a_thread_participant(anonymizer) -> None:
    out = anonymizer.chats(
        [
            chat("1", "Me cobraron dos veces."),
            chat("2", "Ya lo reverso, Manuela.", author="Kelly Castaño"),
            chat("3", "mil gracias!! kelly"),
            chat("4", "ok gracias, le cuento a camila"),
        ]
    )
    surrogate = given_of(anonymizer, "Kelly Castaño").lower()
    assert out[2]["text"] == f"mil gracias!! {surrogate}"
    assert "camila" in out[3]["text"]


def test_everyday_word_names_only_as_vocative(registry: Registry) -> None:
    directory = Directory(registry)
    user = {"id": "U1", "name": "luz.rojas", "real_name": "Luz Rojas Peña", "profile": {}}
    directory.add_seeds(seeds_from_users([user]), {})
    anonymizer = Anonymizer(registry, directory, SECRET)
    out = anonymizer.chats(
        [
            chat("1", "se fue la luz en la casa", author="Luz Rojas Peña"),
            chat("2", "listo, gracias luz", author="Kipi"),
        ]
    )
    assert out[0]["text"] == "se fue la luz en la casa"
    assert not contiene(out[1]["text"].split("gracias")[1], "luz")


def test_display_name_signoff_is_replaced(registry: Registry) -> None:
    directory = Directory(registry)
    user = {
        "id": "U2",
        "name": "yesenia.mora",
        "real_name": "Yesenia Mora Díaz",
        "profile": {"display_name": "Yese", "email": "yesenia.mora@gmail.com"},
    }
    directory.add_seeds(seeds_from_users([user]), {})
    anonymizer = Anonymizer(registry, directory, SECRET)
    out = anonymizer.chats([chat("1", "un abrazo, Yese", author="Yesenia Mora Díaz")])
    assert out[0]["text"] == f"un abrazo, {given_of(anonymizer, 'Yesenia Mora Díaz')}"
    entity = anonymizer.entities(out)[0]
    assert "Yese" in entity["originals"]


def test_cyrillic_lookalikes_are_detected(anonymizer) -> None:
    text = "Soy Mаnuelа Cаstаñо, CC 1107360039"
    out = anonymizer.chats([chat("1", text, author="Kipi")])[0]["text"]
    assert not contiene(out, "Manuela Castaño")
    assert not contiene(out, text.split(",")[0])
