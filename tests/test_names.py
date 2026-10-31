from kipu.detect.names import KeptMatcher, NameIndex


def kinds(index: NameIndex, text: str) -> set[tuple[str, str]]:
    return {(m.text, m.kind) for m in index.scan(text)}


def test_full_short_filename_and_upper_forms(directory) -> None:
    index = NameIndex(directory.persons.values())
    found = kinds(
        index,
        "Hola, me llamo Manuela Castaño. MANUELA CASTANO RUIZ, extracto_manuela_castano_julio.pdf",
    )
    assert ("Manuela Castaño", "short") in found
    assert ("MANUELA CASTANO RUIZ", "full") in found
    assert ("manuela_castano", "short") in found


def test_first_name_must_be_capitalized(directory) -> None:
    index = NameIndex(directory.persons.values())
    assert ("Manuela", "first") in kinds(index, "Hola Manuela")
    assert not any(k == "first" for _, k in kinds(index, "hola manuela"))


def test_handles_and_titled_surnames(directory) -> None:
    index = NameIndex(directory.persons.values())
    assert ("kelly.castano", "short") in kinds(index, "revisado_por: @kelly.castano")
    assert ("Santander", "surname") in kinds(index, "Estimado señor Santander:")
    assert not any(k == "surname" for _, k in kinds(index, "pagué desde Santander"))


def test_external_person_needs_given_name_and_surname(directory) -> None:
    index = NameIndex(directory.persons.values())
    assert ("Pamela Agudelo", "external") in kinds(index, "draft reviewed by Pamela Agudelo (x)")
    assert not any(k == "external" for _, k in kinds(index, "la Plaza Botero"))


def test_kept_terms_respect_case_for_single_words() -> None:
    kept = KeptMatcher()
    assert [m.value for m in kept.scan("pago por PSE desde Bancolombia")] == ["PSE", "Bancolombia"]
    assert not kept.scan("lo hicimos con éxito")
    assert [m.value for m in kept.scan("compra en el Éxito de San Fernando")] == [
        "Éxito",
        "San Fernando",
    ]


def test_names_split_by_a_line_break(directory) -> None:
    index = NameIndex(directory.persons.values())
    assert ("Manuela\nCastaño", "short") in kinds(index, "firma: Manuela\nCastaño, cliente")
    assert ("Jesús\nSánchez", "external") in kinds(index, "representado por Jesús\nSánchez, con")


def test_one_mention_keeps_every_homonym_candidate(directory) -> None:
    directory.external_person("Camila Arango", "")
    index = NameIndex(directory.persons.values())
    mentions = [m for m in index.scan("Revisar a Camila Arango hoy") if m.kind != "external"]
    names = [m for m in mentions if m.text == "Camila Arango"]
    assert len(names) == 1 and len(names[0].candidates) == 3
