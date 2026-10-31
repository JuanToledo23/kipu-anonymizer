from conftest import SECRET, client_seed
from kipu.anonymizer import Anonymizer
from kipu.detect.names import HandleMatcher, KeptMatcher
from kipu.ingest.records import ChatRecord, DocRecord


def handles(directory, text: str) -> list[tuple[str, str | None]]:
    matcher = HandleMatcher(directory.persons.values(), KeptMatcher())
    return [(m.text, m.person_id) for m in matcher.scan(text)]


def manuela(directory) -> str:
    return directory.owner_of("cliente_id", "CL-88627")


def test_handles_inside_urls_and_paths(directory) -> None:
    pid = manuela(directory)
    for text in (
        "https://kipupay.co/credito/simular?ref=mcastano&canal=chat",
        "kit: https://kipupay.co/onboarding/kit?u=MCastano&lang=en",
        "https://kipupay.co/u/mcastano/perfil",
        "usuario mcastano.",
        "reporte_mcastano_2026.pdf",
    ):
        start = text.lower().index("mcastano")
        assert handles(directory, text) == [(text[start : start + 8], pid)], text


def test_handle_needs_token_boundaries(directory) -> None:
    assert handles(directory, "magomez xmcastano mcastano2 kelly.castanos") == []


def test_at_mentions_known_and_unknown(directory) -> None:
    kelly = directory.owner_of("kp_id", "KP-016")
    assert handles(directory, "publica como @kelly.castano; avisen a @pedro.perez.") == [
        ("kelly.castano", kelly),
        ("pedro.perez", None),
    ]
    assert handles(directory, "@here @channel <@U0LAKVS3G> @kipupay") == []


def test_handles_are_replaced_with_the_email_local_part_surrogate(anonymizer) -> None:
    records = [
        ChatRecord(
            id="1",
            thread_id="t:1",
            author="Manuela Castaño",
            ts="1.1",
            text="Te dejo el link: https://kipupay.co/simular?ref=mcastano&canal=chat",
            attachments=[],
        )
    ]
    text = anonymizer.chats(records)[0]["text"]
    assert "mcastano" not in text.lower()
    person = anonymizer.directory.persons[manuela(anonymizer.directory)]
    local = anonymizer.factory.email(person, "mcastano@gmail.com").split("@")[0]
    assert f"?ref={local}&canal=chat" in text


def test_unknown_at_handle_gets_a_new_surrogate(anonymizer) -> None:
    doc = DocRecord(
        id="d1",
        format="md",
        filename="plan.md",
        title="Plan",
        body="Trabajaremos con una creadora que publica como @lucia.rendon y con @here.",
        metadata={},
    )
    body = anonymizer.docs([doc])[0]["body"]
    assert "lucia.rendon" not in body and "@here" in body
    surrogate = body.split("@", 1)[1].split(" ", 1)[0]
    assert "." in surrogate and surrogate != "lucia.rendon"
    entity = next(
        e
        for e in anonymizer.entities([{"id": "d1", "body": body}])
        if "lucia.rendon" in e["originals"]
    )
    assert entity["pseudonyms"]["handle"] == surrogate


def test_shared_handle_goes_to_the_thread_participant(registry, directory) -> None:
    directory.add_seeds(
        [
            client_seed(
                "CL-99999", "Mateo Castaño Rojas", "1000000001", "mcastano@yahoo.com", "3000000001"
            )
        ],
        {},
    )
    anonymizer = Anonymizer(registry, directory, SECRET)
    manuela_id = manuela(directory)
    mateo = directory.owner_of("cliente_id", "CL-99999")
    for author, owner in (("Manuela Castaño", manuela_id), ("Mateo Castaño", mateo)):
        record = ChatRecord(
            id=author,
            thread_id=author,
            author=author,
            ts="1.1",
            text="ref=mcastano",
            attachments=[],
        )
        text = anonymizer.chats([record])[0]["text"]
        person = directory.persons[owner]
        email = next(e for e in person.identifiers["email"] if e.startswith("mcastano@"))
        assert text == "ref=" + anonymizer.factory.handle(person, "mcastano", email), author
