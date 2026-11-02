from pathlib import Path

from conftest import SECRET
from kipu.anonymizer import Anonymizer
from kipu.audit.texto import contiene
from kipu.directory.directory import Directory
from kipu.directory.registry import Registry
from kipu.directory.seed import seeds_from_users
from kipu.ingest.records import ChatRecord
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
            chat("2", "Ya lo reverso, Manuela.", author="Kelly Cardeño"),
            chat("3", "mil gracias!! kelly"),
            chat("4", "ok gracias, le cuento a camila"),
        ]
    )
    surrogate = given_of(anonymizer, "Kelly Cardeño").lower()
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
    text = "Soy Mаnuelа Cаstаñо, CC 1108470149"
    out = anonymizer.chats([chat("1", text, author="Kipi")])[0]["text"]
    assert not contiene(out, "Manuela Cardeño")
    assert not contiene(out, text.split(",")[0])


def test_live_mode_leaves_our_own_surrogates_alone(registry, directory) -> None:
    first = Anonymizer(registry, directory, SECRET)
    text = "Soy Manuela Cardeño, correo mcardeno@gmail.com, cel 3149830663"
    copied = first.chats([chat("1", text, author="Kipi")])[0]
    live = Anonymizer(registry, Directory.from_registry(registry), SECRET, live=True)
    again = live.chats([ChatRecord(**{**copied, "author": "Kipi", "attachments": []})])
    assert again[0]["text"] == copied["text"]
    assert live.entities(again) == []


def bare_mention_owner(anonymizer: Anonymizer, texts: list[str]) -> str | None:
    """Person given to the bare "Camila Osorio" in the last message of a bot-only thread."""
    records = [chat(str(i), text, author="Kipi") for i, text in enumerate(texts)]
    anonymizer.chats(records)
    last = anonymizer.spans[(records[-1].id, "text")]
    return next(m.person_id for m in last if m.text == "Camila Osorio")


def test_bare_homonym_prefers_identifier_owner_over_a_name_elsewhere(anonymizer) -> None:
    velasquez = anonymizer.directory.owner_of("cliente_id", "CL-12538")
    owner = bare_mention_owner(
        anonymizer,
        [
            "Caso de Camila Osorio Mejía cerrado.",
            "Nuevo caso, cédula 1023759618.",
            "Hablé con Camila Osorio y ya quedó.",
        ],
    )
    assert owner == velasquez


def test_bare_homonym_without_evidence_goes_to_the_judge(anonymizer) -> None:
    asked = []

    def judge(name, candidates, group):
        asked.append(name)
        return candidates[-1]

    anonymizer.detector.judge = judge
    owner = bare_mention_owner(anonymizer, ["Hablé con Camila Osorio y ya quedó."])
    assert asked == ["Camila Osorio"]
    assert owner == max(
        anonymizer.directory.owner_of("cliente_id", c) for c in ("CL-12538", "CL-57176")
    )
