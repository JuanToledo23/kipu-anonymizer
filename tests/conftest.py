from pathlib import Path

import pytest

from kipu.anonymizer import Anonymizer
from kipu.directory.directory import Directory
from kipu.directory.registry import Registry
from kipu.directory.seed import PersonSeed

SECRET = "test-secret"


def client_seed(
    key: str, name: str, cedula: str, email: str, phone: str, **attrs: str
) -> PersonSeed:
    seed = PersonSeed(f"clientes:{key}", "client", name, attrs.get("ocupacion", ""))
    seed.add("cliente_id", key)
    seed.add("cedula", cedula)
    seed.add("email", email)
    seed.add("phone", phone)
    seed.add("birth_iso", attrs.pop("birth", "1990-01-01"))
    seed.attrs = attrs
    return seed


def employee_seed(key: str, name: str, email: str, title: str = "Agente de Soporte") -> PersonSeed:
    seed = PersonSeed(f"empleados:{key}", "employee", name, title)
    seed.add("kp_id", key)
    seed.add("email", email)
    return seed


@pytest.fixture
def registry(tmp_path: Path) -> Registry:
    reg = Registry(tmp_path / "registry.sqlite")
    yield reg
    reg.close()


@pytest.fixture
def directory(registry: Registry) -> Directory:
    d = Directory(registry)
    seeds = [
        client_seed(
            "CL-88627",
            "Manuela Cardeño Ruiz",
            "1108470149",
            "mcardeno@gmail.com",
            "3149830663",
            ciudad="Cali",
            barrio="Granada",
            ocupacion="fotógrafa",
            vinculacion="2025-08-21",
        ),
        client_seed(
            "CL-12538",
            "Camila Osorio Velásquez",
            "1023759618",
            "camila.osorio@gmail.com",
            "3517273690",
        ),
        client_seed(
            "CL-57176", "Camila Osorio Mejía", "1046483072", "cosorio@icloud.com", "3187145071"
        ),
        client_seed(
            "CL-21823", "Luis Bolívar Moreno", "91447343", "lbolivar@gmail.com", "3001112233"
        ),
        employee_seed(
            "KP-005", "Luis Bolívar Ruiz", "luis.bolivar@kipupay.co", "Coordinador de Fraude"
        ),
        employee_seed("KP-016", "Kelly Cardeño Jiménez", "kelly.cardeno@kipupay.co", "Contadora"),
    ]
    d.add_seeds(seeds, {})
    return d


@pytest.fixture
def anonymizer(registry: Registry, directory: Directory) -> Anonymizer:
    return Anonymizer(registry, directory, SECRET)
