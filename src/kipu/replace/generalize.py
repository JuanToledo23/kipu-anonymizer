"""Generalization of quasi-identifiers: occupation → sector, barrio → another barrio or the city."""

import hashlib
import hmac

from kipu.directory.names import fold

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
SECTORS_FOLDED = {fold(k): v for k, v in SECTORS.items()}
FALLBACK_SECTOR = "profesional independiente"

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


def sector_for(occupation: str) -> str:
    return SECTORS_FOLDED.get(fold(occupation).strip(), FALLBACK_SECTOR) if occupation else ""


def swap_barrio(secret: str, person_id: str, city: str, barrio: str) -> str:
    """Another barrio of the same city, fixed per person."""
    options = [b for b in BARRIOS.get(city, ()) if fold(b) != fold(barrio)]
    if not options:
        return barrio
    digest = hmac.new(secret.encode(), f"{person_id}|barrio".encode(), hashlib.sha256).digest()
    return options[int.from_bytes(digest[:8], "big") % len(options)]
