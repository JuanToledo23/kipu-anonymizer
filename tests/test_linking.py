from test_render import chat

CLIENT = "Camila Arango Velásquez"


def owner(anonymizer, id_type: str, value: str) -> str | None:
    return anonymizer.directory.owner_of(id_type, value)


def short_of(anonymizer, person_id: str) -> str:
    return anonymizer.factory.name(anonymizer.directory.persons[person_id]).short


def test_known_name_with_a_contradicting_cedula_is_a_new_person(anonymizer) -> None:
    manuela = owner(anonymizer, "cedula", "1107360039")
    out = anonymizer.chats([chat("1", "Soy Manuela Castaño Ruiz, CC 52123456", author=CLIENT)])
    homonym = owner(anonymizer, "cedula", "52123456")
    assert homonym not in (None, manuela, anonymizer.author_person(CLIENT))
    assert short_of(anonymizer, homonym) in out[0]["text"]
    assert short_of(anonymizer, manuela) not in out[0]["text"]


def test_known_name_with_a_first_phone_is_the_same_person(anonymizer) -> None:
    kelly = owner(anonymizer, "kp_id", "KP-016")
    anonymizer.chats([chat("1", "Kelly Castaño Jiménez, cel 3001234567", author=CLIENT)])
    assert owner(anonymizer, "phone", "3001234567") == kelly


def test_two_homonyms_in_one_batch_are_two_persons(anonymizer) -> None:
    out = anonymizer.chats(
        [
            chat("1", "Soy Héctor Fabio Gómez Rentería, CC 94512367", author="Kipi"),
            chat("2", "Soy Héctor Fabio Gómez Rentería, CC 1023456781", author="Kipi"),
        ]
    )
    first, second = (
        owner(anonymizer, "cedula", "94512367"),
        owner(anonymizer, "cedula", "1023456781"),
    )
    assert None not in (first, second) and first != second
    assert short_of(anonymizer, first) in out[0]["text"]
    assert short_of(anonymizer, second) in out[1]["text"]
    assert short_of(anonymizer, first) != short_of(anonymizer, second)


def test_identifier_links_to_a_name_up_to_forty_characters_before(anonymizer) -> None:
    kelly = owner(anonymizer, "kp_id", "KP-016")
    author = anonymizer.author_person(CLIENT)
    anonymizer.chats(
        [
            chat("1", "me llamo Kelly Castaño Jiménez y mi cédula es 1012345678", author=CLIENT),
            chat("2", "Kelly Castaño Jiménez (CC 1012345679)", author=CLIENT),
            chat(
                "3",
                "Kelly Castaño Jiménez me atendió muy bien ayer en la tarde, mi cel 3109998877",
                author=CLIENT,
            ),
        ]
    )
    assert owner(anonymizer, "cedula", "1012345678") == kelly
    assert owner(anonymizer, "cedula", "1012345679") not in (kelly, author)
    assert owner(anonymizer, "phone", "3109998877") == author


def test_unknown_name_keeps_an_unlisted_given_name_and_second_surname(anonymizer) -> None:
    out = anonymizer.chats(
        [chat("1", "Hola, soy Wendy Patricia Ochoa Lizcano, CC 1098765432", author="Kipi")]
    )[0]["text"]
    assert "Wendy" not in out and "Lizcano" not in out


def test_kept_terms_are_never_widened(anonymizer) -> None:
    out = anonymizer.chats([chat("1", "Compra Juan Valdez Boston", author="Kipi")])
    assert out[0]["text"] == "Compra Juan Valdez Boston"
