import json
import shutil
import subprocess
from pathlib import Path

import pytest

from kipu.audit.texto import contiene, digitos_compactos, norm

HARNESS = Path(__file__).parent / "js" / "texto_harness.mjs"

NORM_CASES = [
    "Manuela Castaño",
    "MANUELA CASTANO",
    "Ｍａｎｕｅｌａ",
    "Mаnuela",  # Cyrillic a
    "Ma​nuela",
    "Straße ſ ς",
    "José María",
    "  dos   espacios ",
    "İstanbul",
    "ﬁ ligadura",
]
DIGIT_CASES = ["1.107.360.039", "+57 321 252 4068", "313-207-1166", "a1b2", "3 1 4"]
CONTAINS_CASES = [
    ("Hola, me llamo Manuela Castaño.", "Manuela Castaño"),
    ("HOLA MANUELA CASTANO", "Manuela Castaño"),
    ("ManuelaCastaño", "Manuela Castaño"),
    ("Manuela  Castaño", "Manuela Castaño"),
    ("Manuela Castañoz", "Manuela Castaño"),
    ("extracto_manuela_castano_julio.pdf", "manuela_castano"),
    ("extracto manuela castano", "manuela_castano"),
    ("Claro, es 1.107.360.039", "1107360039"),
    ("Claro, es 1107360039", "1.107.360.039"),
    ("cc 11073600390", "1107360039"),
    ("llámame al +57 314 872 9552", "3148729552"),
    ("llámame al 573148729552", "3148729552"),
    ("llámame al 314-872-9552", "573148729552"),
    ("mcastano@gmail.com", "mcastano@gmail.com"),
    ("MCASTANO@GMAIL.COM", "mcastano@gmail.com"),
    ("mcastano arroba gmail punto com", "mcastano@gmail.com"),
    ("Diagonal 3A # 78-6 apto 1501", "Diagonal 3A # 78-6 apto 1501"),
    ("diagonal 3a # 78-6 apto 1501", "Diagonal 3A # 78-6 apto 1501"),
    ("nacida el 1997-07-23", "1997-07-23"),
    ("nacida el 23/07/1997", "1997-07-23"),
    ("Yesenia", "Yesenia"),
    ("Yesenias", "Yesenia"),
    ("Mаnuela Castaño", "Manuela Castaño"),
    ("Manu​ela Castaño", "Manuela Castaño"),
    ("placa LTE758", "LTE758"),
    ("placa lte 758", "LTE758"),
    ("pasaporte AP395977", "AP395977"),
    ("Bancolombia", "Bancolombia"),
    ("Sr. Ló", "Ló"),
    ("anything", ""),
    ("CC1022649507_certificado.pdf", "1022649507"),
    ("al 3148729552", "3148729552"),
    ("10.22.649.507", "1022649507"),
    ("Ana Lucía Marulanda", "Ana Lucía Marulanda Hoyos"),
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
