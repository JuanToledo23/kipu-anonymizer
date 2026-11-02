from kipu.detect.names import KeptMatcher, NameIndex


def kinds(index: NameIndex, text: str) -> set[tuple[str, str]]:
    return {(m.text, m.kind) for m in index.scan(text)}


def test_full_short_filename_and_upper_forms(directory) -> None:
    index = NameIndex(directory.persons.values())
    found = kinds(
        index,
        "Hola, me llamo Manuela Cardeño. MANUELA CARDENO RUIZ, extracto_manuela_cardeno_julio.pdf",
    )
    assert ("Manuela Cardeño", "short") in found
    assert ("MANUELA CARDENO RUIZ", "full") in found
    assert ("manuela_cardeno", "short") in found


def test_first_name_must_be_capitalized(directory) -> None:
    index = NameIndex(directory.persons.values())
    assert ("Manuela", "first") in kinds(index, "Hola Manuela")
    assert not any(k == "first" for _, k in kinds(index, "hola manuela"))


def test_handles_and_titled_surnames(directory) -> None:
    index = NameIndex(directory.persons.values())
    assert ("kelly.cardeno", "short") in kinds(index, "revisado_por: @kelly.cardeno")
    assert ("Bolívar", "surname") in kinds(index, "Estimado señor Bolívar:")
    assert not any(k == "surname" for _, k in kinds(index, "pagué desde Bolívar"))


def test_external_person_needs_given_name_and_surname(directory) -> None:
    index = NameIndex(directory.persons.values())
    assert ("Pamela Cifuentes", "external") in kinds(
        index, "draft reviewed by Pamela Cifuentes (x)"
    )
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
    assert ("Manuela\nCardeño", "short") in kinds(index, "firma: Manuela\nCardeño, cliente")
    assert ("Jesús\nQuiroga", "external") in kinds(index, "representado por Jesús\nQuiroga, con")


def test_one_mention_keeps_every_homonym_candidate(directory) -> None:
    directory.external_person("Camila Osorio", "")
    index = NameIndex(directory.persons.values())
    mentions = [m for m in index.scan("Revisar a Camila Osorio hoy") if m.kind != "external"]
    names = [m for m in mentions if m.text == "Camila Osorio"]
    assert len(names) == 1 and len(names[0].candidates) == 3
