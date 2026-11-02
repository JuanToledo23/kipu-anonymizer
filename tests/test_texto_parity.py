import json
import shutil
import subprocess
from pathlib import Path

import pytest

from kipu.audit.texto import contiene, digitos_compactos, norm

HARNESS = Path(__file__).parent / "js" / "texto_harness.mjs"

NORM_CASES = [
    "Manuela Cardeño",
    "MANUELA CARDENO",
    "Ｍａｎｕｅｌａ",
    "Mаnuela",  # Cyrillic a
    "Ma​nuela",
    "Straße ſ ς",
    "José María",
    "  dos   espacios ",
    "İstanbul",
    "ﬁ ligadura",
]
DIGIT_CASES = ["1.108.470.149", "+57 321 252 4068", "313-208-4417", "a1b2", "3 1 4"]
CONTAINS_CASES = [
    ("Hola, me llamo Manuela Cardeño.", "Manuela Cardeño"),
    ("HOLA MANUELA CARDENO", "Manuela Cardeño"),
    ("ManuelaCardeño", "Manuela Cardeño"),
    ("Manuela  Cardeño", "Manuela Cardeño"),
    ("Manuela Cardeñoz", "Manuela Cardeño"),
    ("extracto_manuela_cardeno_julio.pdf", "manuela_cardeno"),
    ("extracto manuela cardeno", "manuela_cardeno"),
    ("Claro, es 1.108.470.149", "1108470149"),
    ("Claro, es 1108470149", "1.108.470.149"),
    ("cc 11084701490", "1108470149"),
    ("llámame al +57 314 983 0663", "3149830663"),
    ("llámame al 573149830663", "3149830663"),
    ("llámame al 314-983-0663", "573149830663"),
    ("mcardeno@gmail.com", "mcardeno@gmail.com"),
    ("MCARDENO@GMAIL.COM", "mcardeno@gmail.com"),
    ("mcardeno arroba gmail punto com", "mcardeno@gmail.com"),
    ("Diagonal 5B # 64-9 apto 1702", "Diagonal 5B # 64-9 apto 1702"),
    ("diagonal 5b # 64-9 apto 1702", "Diagonal 5B # 64-9 apto 1702"),
    ("nacida el 1997-07-23", "1997-07-23"),
    ("nacida el 23/07/1997", "1997-07-23"),
    ("Yesenia", "Yesenia"),
    ("Yesenias", "Yesenia"),
    ("Mаnuela Cardeño", "Manuela Cardeño"),
    ("Manu​ela Cardeño", "Manuela Cardeño"),
    ("placa LRK204", "LRK204"),
    ("placa lrk 204", "LRK204"),
    ("pasaporte AP506088", "AP506088"),
    ("Bancolombia", "Bancolombia"),
    ("Sr. Ló", "Ló"),
    ("anything", ""),
    ("CC1023759618_certificado.pdf", "1023759618"),
    ("al 3149830663", "3149830663"),
    ("10.22.649.507", "1023759618"),
    ("Ana Lucía Valderrama", "Ana Lucía Valderrama Hoyos"),
]


def _node_results(cases: list[list]) -> list:
    proc = subprocess.run(
        ["node", str(HARNESS)],
        input=json.dumps(cases),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_python_port_matches_validator() -> None:
    cases: list[list] = [["norm", s] for s in NORM_CASES]
    cases += [["digitosCompactos", s] for s in DIGIT_CASES]
    cases += [["contiene", h, n] for h, n in CONTAINS_CASES]
    expected = _node_results(cases)
    python_fns = {"norm": norm, "digitosCompactos": digitos_compactos, "contiene": contiene}
    actual = [python_fns[fn](*args) for fn, *args in cases]
    mismatches = [(c, e, a) for c, e, a in zip(cases, expected, actual, strict=True) if e != a]
    assert not mismatches
    assert len(cases) >= 40
