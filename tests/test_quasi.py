import pytest

from kipu.detect.quasi import QuasiLexicon
from kipu.directory.directory import Person
from kipu.directory.names import split_name
from kipu.replace.generalize import inflections, plural_phrase
from kipu.replace.render import apply_mentions


def person(pid: str, kind: str = "client", **attrs: str) -> Person:
    return Person(pid, kind, "F", split_name("Ana Pérez"), attrs=attrs)


PERSONS = [
    person("p_1", ciudad="Cali", barrio="Granada", ocupacion="fotógrafa"),
    person("p_2", ciudad="Medellín", barrio="Castilla", ocupacion="contador"),
    person("p_3", ciudad="Bogotá", barrio="Castilla", ocupacion="abogada"),
    person("p_4", "employee", cargo="Analista de Fraude", ciudad="Bogotá", ingreso="2022"),
    person("p_5", "employee", cargo="Agente de Soporte", ciudad="Cali", ingreso="2024"),
    person("p_6", "employee", cargo="Agente de Soporte", ciudad="Cali", ingreso="2024"),
    person("p_7", "employee", cargo="CEO", ciudad="Medellín", ingreso="2019"),
]


@pytest.fixture(scope="module")
def lexicon() -> QuasiLexicon:
    return QuasiLexicon(PERSONS, kept=["Envigado"])


class Replace:
    def render(self, m):
        return m.value


def generalize(lexicon: QuasiLexicon, text: str, context: list[Person] = ()) -> str:
    return apply_mentions(text, lexicon.scan(text, list(context)), Replace())


def test_inflections_and_plural_sectors() -> None:
    assert set(inflections("diseñadora gráfica")) >= {
        "disenadora grafica",
        "disenador grafico",
        "disenadores graficos",
    }
    assert set(inflections("Analista de Fraude")) == {"analista de fraude", "analistas de fraude"}
    assert plural_phrase("profesional de la salud") == "profesionales de la salud"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "soy fotógrafo, vivo en Granada. y llevo con ustedes desde 2021 sin atrasos",
            "soy profesional de las industrias creativas, vivo en Cali. y llevo con ustedes "
            "desde hace unos años sin atrasos",
        ),
        (
            "Es contadora, vinculada en 2023.",
            "Es profesional de finanzas, vinculada en hace unos años.",
        ),
        ("Las enfermeras del turno", "Las profesionales de la salud del turno"),
        ("Retiro en cajero del barrio Galán", "Retiro en cajero de Bogotá"),
        ("Calle 1 # 2-3, Castilla, Medellín.", "Calle 1 # 2-3, Medellín."),
        ("tengo 54 años y nunca", "tengo cincuenta y tantos años y nunca"),
        ("Cargo: Analista de Fraude", "Cargo: Analista"),
        ("aprobado por el CEO.", "aprobado por el directivo."),
        ("cliente desde el '24", "cliente desde hace unos años"),
    ],
)
def test_generalized_everywhere(lexicon: QuasiLexicon, text: str, expected: str) -> None:
    assert generalize(lexicon, text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "la señora Restrepo llamó",  # a surname, not after a place cue
        "en Granada Hills",  # part of a longer proper name
        "vivo en Envigado",  # a kept place
        "las oficinas de Kipu quedan en El Poblado",
        "El certificado de 2025 ya salió",  # a year without a joining cue
        "acuerdo de pago desde el 22 de septiembre",
        "desde el 13 de octubre de 2025",
        "incapacidad médica certificada",
        "Firma: Auditores Andinos",
        "hace 3 años y tengo 5 años de cliente",
        "Cargo: Agente de Soporte",  # not unique with city and year
    ],
)
def test_left_untouched(lexicon: QuasiLexicon, text: str) -> None:
    assert generalize(lexicon, text) == text


def test_ambiguous_barrio_takes_the_linked_city(lexicon: QuasiLexicon) -> None:
    assert generalize(lexicon, "vivo en Castilla", [PERSONS[1]]) == "vivo en Medellín"
    assert generalize(lexicon, "vivo en Castilla", [PERSONS[2]]) == "vivo en Bogotá"
    assert generalize(lexicon, "Compra D1 Granada", [PERSONS[0]]) == "Compra D1 Cali"
    assert generalize(lexicon, "Compra D1 Granada") == "Compra D1 Granada"
    envigado = person("p_8", ciudad="Medellín", barrio="Envigado", vinculacion="2021-02-01")
    assert generalize(lexicon, "vivo en Envigado", [envigado]) == "vivo en Medellín"


def test_a_linked_persons_join_year_goes_with_their_components(lexicon: QuasiLexicon) -> None:
    text = "Arquitecto. Crédito de consumo 2021 cancelado."
    linked = person("p_9", ciudad="Cali", ocupacion="arquitecto", vinculacion="2021-05-04")
    assert generalize(lexicon, text, [linked]) == (
        "Profesional de la construcción. Crédito de consumo hace unos años cancelado."
    )
    assert generalize(lexicon, "Crédito de consumo 2021 cancelado.", [linked]) == (
        "Crédito de consumo 2021 cancelado."
    )
    assert generalize(lexicon, "en Medellín, cerca de Castilla") == "en Medellín, cerca de Medellín"


def test_front_matter_keys_are_kept(lexicon: QuasiLexicon) -> None:
    text = "---\nabogada: x@y.co\n---\nLa abogada revisó."
    assert (
        generalize(lexicon, text)
        == "---\nabogada: x@y.co\n---\nLa profesional del sector jurídico revisó."
    )
