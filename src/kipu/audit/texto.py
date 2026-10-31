"""Python port of the text normalization in validar.mjs (`norm`, `digitosCompactos`, `contiene`).

Kept deliberately close to the JS source so the parity test can compare both line by line.
"""

import unicodedata
from functools import lru_cache

import regex

ESPACIO = r"\t\n\v\f\r \x1c-\x1f\x85\xa0  -     　"
PALABRA = r"\p{L}\p{N}_"
CONFUSABLES = {
    "а": "a",
    "е": "e",
    "о": "o",
    "р": "p",
    "с": "c",
    "х": "x",
    "у": "y",
    "і": "i",
    "ј": "j",
    "ѕ": "s",
    "А": "A",
    "Е": "E",
    "О": "O",
    "Р": "P",
    "С": "C",
    "Х": "X",
    "У": "Y",
    "І": "I",
    "Ј": "J",
    "Ѕ": "S",
    "В": "B",
    "Н": "H",
    "К": "K",
    "М": "M",
    "Т": "T",
    "ο": "o",
    "α": "a",
    "ρ": "p",
    "ν": "v",
    "ι": "i",
    "ℓ": "l",
    "ı": "i",
    "ɑ": "a",
}
RX_CONFUSABLE = regex.compile("[" + "".join(CONFUSABLES) + "]")
RX_FORMATO = regex.compile(r"\p{Cf}")
RX_ESPACIO_RARO = regex.compile(r"[\p{Zs}\t\r\n\v\f]")
RX_ESPACIOS = regex.compile(r" {2,}")
RX_MARCA = regex.compile(r"\p{Mn}")
RX_SEPARADOR_DIGITOS = regex.compile(rf"(?<=\p{{Nd}})[{ESPACIO}.,'’`\xb4\xb7/\-–—_](?=\p{{Nd}})")
RX_NO_DIGITO = regex.compile(r"\P{Nd}")
RX_TEXTO = regex.compile(rf"^[{PALABRA} .'_-]+$")
RX_BLANCOS = regex.compile(rf"[{ESPACIO}]+")
RX_ASCII_LETRA = regex.compile(r"[a-zA-Z]")
RX_MINUSCULA = regex.compile(r"[a-z]")


def escapar_regex(s: str) -> str:
    return regex.sub(r"[.*+?^${}()|\[\]\\/]", lambda m: "\\" + m.group(0), s)


def partir(s: str) -> list[str]:
    return [x for x in RX_BLANCOS.split(s) if x != ""]


def plegar(s: str) -> str:
    return s.lower().replace("ß", "ss").replace("ς", "σ").replace("ſ", "s")


def quitar_tildes(s: str) -> str:
    return RX_MARCA.sub("", unicodedata.normalize("NFD", s))


def clean(s: str) -> str:
    base = RX_CONFUSABLE.sub(
        lambda m: CONFUSABLES.get(m.group(0), m.group(0)), unicodedata.normalize("NFKC", s)
    )
    out = []
    for c in base:
        if RX_FORMATO.match(c):
            continue
        out.append(" " if RX_ESPACIO_RARO.match(c) else c)
    return RX_ESPACIOS.sub(" ", "".join(out))


@lru_cache(maxsize=200_000)
def norm(s: str) -> str:
    return plegar(quitar_tildes(clean(s)))


@lru_cache(maxsize=200_000)
def digitos_compactos(s: str) -> str:
    return RX_SEPARADOR_DIGITOS.sub("", clean(s))


def solo_digitos(s: str) -> str:
    return RX_NO_DIGITO.sub("", s)


@lru_cache(maxsize=50_000)
def _rx(fuente: str) -> regex.Pattern[str]:
    return regex.compile(fuente)


def digitos_en(haystack: str, digitos: str) -> bool:
    compacto = digitos_compactos(haystack)
    candidatos = [digitos]
    if len(digitos) == 12 and digitos.startswith("57"):
        candidatos.append(digitos[2:])
    return any(
        _rx(rf"(?<!\p{{Nd}})(?:57)?{escapar_regex(d)}(?!\p{{Nd}})").search(compacto)
        for d in candidatos
    )


def contiene(haystack: str, needle: str, exacto: bool = False) -> bool:
    """True if `needle` survives in `haystack` under the judge's tolerant comparison."""
    if not needle:
        return False
    if exacto:
        return needle in haystack
    digitos = solo_digitos(needle)
    if (
        digitos
        and len(digitos) >= 6
        and len(digitos) >= 0.6 * len(needle)
        and not RX_ASCII_LETRA.search(needle)
    ):
        return digitos_en(haystack, digitos)
    h = norm(haystack)
    n = norm(needle)
    if RX_TEXTO.match(n) and RX_MINUSCULA.search(n):
        if _rx(rf"(?<![a-z0-9]){escapar_regex(n)}(?![a-z0-9])").search(h):
            return True
        palabras = partir(n)
        if len(palabras) >= 2 and all(len(w) >= 3 for w in palabras):
            patron = f"[{ESPACIO}]*".join(escapar_regex(w) for w in palabras)
            return bool(_rx(rf"(?<![a-z0-9]){patron}(?![a-z0-9])").search(h))
        return False
    return n in h
