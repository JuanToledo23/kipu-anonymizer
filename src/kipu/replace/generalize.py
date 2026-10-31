"""Generalization of quasi-identifiers: occupation → sector, barrio → another barrio or the city."""

import hashlib
import hmac
from pathlib import Path

from kipu.directory.names import fold

OCCUPATIONS_PATH = Path(__file__).with_name("occupations_co.txt")

SECTORS = {
    "abogada": "profesional del sector jurídico",
    "abogado": "profesional del sector jurídico",
    "estudiante de derecho": "estudiante universitario",
    "estudiante de medicina": "estudiante universitario",
    "administrador": "profesional de servicios",
    "administradora": "profesional de servicios",
    "arquitecta": "profesional de la construcción",
    "arquitecto": "profesional de la construcción",
    "ingeniero civil": "profesional de la construcción",
    "ingeniera civil": "profesional de la construcción",
    "electricista": "profesional de la construcción",
    "auxiliar contable": "profesional de finanzas",
    "contador": "profesional de finanzas",
    "contadora": "profesional de finanzas",
    "comerciante": "profesional del comercio",
    "community manager": "profesional de medios",
    "periodista": "profesional de medios",
    "conductor de plataforma": "profesional del transporte",
    "mecánico": "profesional del transporte",
    "mecánica": "profesional del transporte",
    "piloto comercial": "profesional del transporte",
    "chef": "profesional de gastronomía",
    "panadero": "profesional de gastronomía",
    "panadera": "profesional de gastronomía",
    "diseñador gráfico": "profesional de las industrias creativas",
    "diseñadora gráfica": "profesional de las industrias creativas",
    "fotógrafo": "profesional de las industrias creativas",
    "fotógrafa": "profesional de las industrias creativas",
    "docente": "profesional de la educación",
    "enfermera": "profesional de la salud",
    "enfermero": "profesional de la salud",
    "fisioterapeuta": "profesional de la salud",
    "odontóloga": "profesional de la salud",
    "odontólogo": "profesional de la salud",
    "psicóloga": "profesional de la salud",
    "psicólogo": "profesional de la salud",
    "veterinaria": "profesional de la salud",
    "veterinario": "profesional de la salud",
    "estilista": "profesional del cuidado personal",
    "ingeniera de sistemas": "profesional de tecnología",
    "ingeniero de sistemas": "profesional de tecnología",
}
FALLBACK_SECTOR = "profesional independiente"
GENERALIZED_YEAR = "hace unos años"
# Spoken decade for an age: "tengo 34 años" → "tengo treinta y tantos años".
AGE_DECADES = {
    1: "diecitantos",
    2: "veintitantos",
    3: "treinta y tantos",
    4: "cuarenta y tantos",
    5: "cincuenta y tantos",
    6: "sesenta y tantos",
    7: "setenta y tantos",
    8: "ochenta y tantos",
    9: "noventa y tantos",
}
# Cargos without a head word to keep ("Analista de Fraude" → "Analista").
CARGO_FAMILIES = {
    "ceo": "directivo",
    "cto": "directivo",
    "cfo": "directivo",
    "coo": "directivo",
    "product manager": "gerente",
}

BARRIOS = {
    "Bogotá": (
        "Teusaquillo",
        "Cedritos",
        "Castilla",
        "Chapinero",
        "Santa Fe",
        "Galán",
        "Restrepo",
        "La Candelaria",
        "Kennedy",
        "Chicó",
        "Suba",
        "Usaquén",
        "Modelia",
        "Normandía",
    ),
    "Medellín": (
        "Buenos Aires",
        "Castilla",
        "Robledo",
        "El Poblado",
        "Laureles",
        "Manrique",
        "Boston",
        "La América",
        "Belén",
        "Envigado",
        "Itagüí",
        "Sabaneta",
        "Aranjuez",
        "Calasanz",
    ),
    "Cali": (
        "Ciudad Jardín",
        "Santa Mónica",
        "El Peñón",
        "San Fernando",
        "Granada",
        "Tequendama",
        "El Ingenio",
        "San Antonio",
    ),
    "Barranquilla": (
        "Villa Country",
        "El Prado",
        "Riomar",
        "Boston",
        "Alto Prado",
        "Ciudad Jardín",
    ),
    "Cartagena": (
        "Getsemaní",
        "Manga",
        "Bocagrande",
        "Pie de la Popa",
        "Crespo",
        "Castillogrande",
        "El Cabrero",
    ),
    "Bucaramanga": (
        "Sotomayor",
        "Cabecera",
        "Lagos del Cacique",
        "Real de Minas",
        "Cañaveral",
        "San Alonso",
    ),
    "Pereira": ("Villa Olímpica", "Dosquebradas", "Cuba", "Circunvalar", "Álamos", "Pinares"),
}

# Well-known barrios beyond the swap lists above, only for finding them in free text.
KNOWN_BARRIOS = {
    "Bogotá": (
        "Rosales",
        "Salitre",
        "Ciudad Salitre",
        "Fontibón",
        "Engativá",
        "Bosa",
        "Tunjuelito",
        "Santa Bárbara",
        "La Soledad",
        "Galerías",
        "Niza",
        "Colina Campestre",
        "Mazurén",
        "Hayuelos",
        "Timiza",
        "Barrios Unidos",
        "Quinta Paredes",
        "Puente Aranda",
        "Las Cruces",
        "Bella Suiza",
        "Santa Isabel",
        "La Macarena",
        "El Nogal",
        "Chapinero Alto",
        "Quinta Camacho",
    ),
    "Medellín": (
        "Conquistadores",
        "Florida Nueva",
        "Prado Centro",
        "Villa Hermosa",
        "San Javier",
        "Guayabal",
        "Provenza",
        "Manila",
        "El Tesoro",
        "Los Colores",
        "La Floresta",
        "Castropol",
        "Loma de los Bernal",
        "Ciudad del Río",
        "San Diego",
    ),
    "Cali": (
        "Normandía",
        "Versalles",
        "Juanambú",
        "Pance",
        "Valle del Lili",
        "El Refugio",
        "Santa Teresita",
        "Siloé",
        "Aguablanca",
        "San Cayetano",
        "Champagnat",
        "El Limonar",
        "Ciudad Córdoba",
        "Los Cristales",
    ),
    "Barranquilla": (
        "Villa Santos",
        "Altos de Riomar",
        "El Golf",
        "Bellavista",
        "Barrio Abajo",
        "Rebolo",
        "San Felipe",
        "La Concepción",
        "Granadillo",
        "Villa Carolina",
        "El Recreo",
        "Los Nogales",
    ),
    "Bucaramanga": (
        "La Concordia",
        "Mejoras Públicas",
        "Altos de Cabecera",
        "Ciudadela Real de Minas",
        "Provenza",
    ),
    "Cartagena": (
        "El Laguito",
        "Marbella",
        "Pie del Cerro",
        "Torices",
        "La Matuna",
        "Chambacú",
        "Serena del Mar",
    ),
    "Pereira": (
        "Pinares de San Martín",
        "Maraya",
        "Belmonte",
        "Cerritos",
        "San Joaquín",
    ),
    "Manizales": (
        "Chipre",
        "La Enea",
        "Palogrande",
        "Laureles",
        "Versalles",
        "La Francia",
        "Los Rosales",
        "El Cable",
        "Bosques del Norte",
        "La Sultana",
        "San Jorge",
    ),
}


def _common_occupations(path: Path) -> dict[str, str]:
    table = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        forms, _, sector = line.partition(";")
        for form in forms.split(","):
            table[form.strip()] = sector.strip()
    return table


COMMON_SECTORS = _common_occupations(OCCUPATIONS_PATH)
SECTORS_FOLDED = {fold(k): v for k, v in SECTORS.items()}
OCCUPATION_SECTORS = {fold(k): v for k, v in {**COMMON_SECTORS, **SECTORS}.items()}
HEAD_END = frozenset({"de", "del", "en"})


def plural(word: str) -> list[str]:
    """Spanish plural first; a consonant ending also gets -s (chef → chefs)."""
    if word[-1] in "aeiouáéíóú":
        return [word + "s"]
    if word.endswith("z"):
        return [word[:-1] + "ces"]
    return [word + "es", word + "s"]


def swap_gender(word: str) -> str:
    if word.endswith("ora"):
        return word[:-1]
    if word.endswith("or"):
        return word + "a"
    if word.endswith("a") and not word.endswith(("ista", "euta")):
        return word[:-1] + "o"
    if word.endswith("o"):
        return word[:-1] + "a"
    return word


def inflections(term: str, gender: bool = True) -> dict[str, bool]:
    """Folded forms of an occupation or cargo → whether the form is plural. The words before
    "de" agree: both genders (unless `gender` is False) and the plural
    ("diseñadora gráfica" → "disenador grafico", "disenadoras graficas", …)."""
    words = fold(term).split()
    if not words:
        return {}
    cut = next((i for i, w in enumerate(words) if w in HEAD_END), len(words))
    head, tail = words[:cut], words[cut:]
    heads = [head] + ([[swap_gender(w) for w in head]] if gender else [])
    forms: dict[str, bool] = {}
    for h in heads:
        forms.setdefault(" ".join([*h, *tail]), False)
        plurals = [plural(w) for w in h]
        for i in range(max(len(p) for p in plurals)):
            words_pl = [p[min(i, len(p) - 1)] for p in plurals]
            forms.setdefault(" ".join([*words_pl, *tail]), True)
    return forms


def plural_phrase(phrase: str) -> str:
    """Plural of a sector phrase: its words before "de" ("profesionales de la salud")."""
    words = phrase.split()
    cut = next((i for i, w in enumerate(words) if w in HEAD_END), len(words))
    return " ".join([plural(w)[0] for w in words[:cut]] + words[cut:])


def age_phrase(age: int) -> str | None:
    return AGE_DECADES.get(age // 10)


def sector_for(occupation: str, extra: dict[str, str] | None = None) -> str:
    """The mapping table first, then `extra` (LLM classifications of unseen occupations)."""
    if not occupation:
        return ""
    key = fold(occupation).strip()
    return OCCUPATION_SECTORS.get(key) or (extra or {}).get(key) or FALLBACK_SECTOR


def swap_barrio(secret: str, person_id: str, city: str, barrio: str) -> str:
    """Another barrio of the same city, fixed per person."""
    options = [b for b in BARRIOS.get(city, ()) if fold(b) != fold(barrio)]
    if not options:
        return barrio
    digest = hmac.new(secret.encode(), f"{person_id}|barrio".encode(), hashlib.sha256).digest()
    return options[int.from_bytes(digest[:8], "big") % len(options)]
