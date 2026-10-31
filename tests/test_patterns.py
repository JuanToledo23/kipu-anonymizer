from kipu.detect.patterns import (
    find_addresses,
    find_birth_dates,
    find_emails,
    find_numbers,
    find_passports,
    find_plates,
    find_slack_mentions,
)


def labels(mentions) -> list[tuple[str, str]]:
    return [(m.label, m.value) for m in mentions]


def test_plain_and_spelled_emails() -> None:
    text = "escribe a mcastano@gmail.com o a camila punto arango arroba gmail punto com"
    found = list(find_emails(text))
    assert [m.value for m in found] == ["mcastano@gmail.com", "camila.arango@gmail.com"]
    assert found[1].kind == "spelled"


def test_english_spelled_email_needs_a_domain() -> None:
    assert not list(find_emails("my card got declined at a Walgreens in Miami"))
    assert [m.value for m in find_emails("jdoe at gmail dot com")] == ["jdoe@gmail.com"]


def test_cedula_needs_context_or_request() -> None:
    assert labels(find_numbers("Mi cédula es 1.022.029.325")) == [("CEDULA", "1022029325")]
    assert labels(find_numbers("A nombre de Andrés, cc 1010254412.")) == [("CEDULA", "1010254412")]
    assert labels(find_numbers("Claro, es 1.107.360.039", cedula_requested=True)) == [
        ("CEDULA", "1107360039")
    ]
    assert not list(find_numbers("Julio cerró con 1.240 pagos y 7.500.000 pesos"))
    assert not list(find_numbers("crédito 4471-0150 al día"))


def test_phone_shapes() -> None:
    for text in ("+57 321 252 4068", "313-207-1166", "3148729552", "57 3148729552"):
        found = list(find_numbers(text))
        assert found and found[0].label == "PHONE", text
        assert len(found[0].value) == 10


def test_plates_passports_and_slack() -> None:
    assert labels(find_plates("placa LTE758 y moto ABC12D, SOAT_GTD596_2026.pdf")) == [
        ("PLATE", "LTE758"),
        ("PLATE", "ABC12D"),
        ("PLATE", "GTD596"),
    ]
    assert labels(find_passports("my passport number is AP395977 ok")) == [("PASSPORT", "AP395977")]
    assert labels(find_slack_mentions("pidan los logs a <@U0LAKVS3G>.")) == [("SLACK", "U0LAKVS3G")]


def test_birth_dates_iso_and_slashes() -> None:
    found = list(
        find_birth_dates("DOB 01/23/1978, nacida 1997-07-23, evento 2026-08-01, 23/07/1997")
    )
    assert [(m.kind, m.value) for m in found] == [
        ("iso", "1997-07-23"),
        ("mdy", "1978-01-23"),
        ("dmy", "1997-07-23"),
    ]


def test_addresses() -> None:
    found = list(find_addresses("Vivo en Cl 47 # 41-29 apto 1001, Barranquilla."))
    assert [m.text for m in found] == ["Cl 47 # 41-29 apto 1001"]
