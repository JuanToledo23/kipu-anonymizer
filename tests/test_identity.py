from conftest import client_seed
from kipu.directory.directory import Directory
from kipu.directory.seed import PersonSeed


def user_seed(uid: str, name: str, handle: str, email: str = "") -> PersonSeed:
    seed = PersonSeed(f"users:{uid}", "client_user", name, "Cliente")
    seed.add("uid", uid)
    seed.add("handle", handle)
    seed.add("email", email)
    return seed


def test_strong_keys_join_and_homonyms_stay_apart(registry) -> None:
    d = Directory(registry)
    d.add_seeds(
        [
            client_seed(
                "CL-1",
                "Camila Arango Velásquez",
                "1022649507",
                "camila.arango@gmail.com",
                "3506162589",
            ),
            client_seed(
                "CL-2", "Camila Arango Mejía", "1035372961", "carango@icloud.com", "3176034960"
            ),
            user_seed("U1", "Camila Arango", "carango"),
            user_seed("U2", "Camila Arango", "camila.arango"),
        ],
        {},
    )
    assert len(d.persons) == 2
    by_uid = {uid: d.owner_of("uid", uid) for uid in ("U1", "U2")}
    assert by_uid["U1"] == d.owner_of("cliente_id", "CL-2")
    assert by_uid["U2"] == d.owner_of("cliente_id", "CL-1")


def test_different_cedulas_cannot_link(registry) -> None:
    d = Directory(registry)
    a = client_seed("CL-1", "Ana Ruiz", "111111", "ana@gmail.com", "3001111111")
    b = client_seed("CL-2", "Ana Ruiz", "222222", "ana@gmail.com", "3002222222")
    d.add_seeds([a, b], {})
    assert len(d.persons) == 2


def test_registry_ids_are_stable_across_runs(registry) -> None:
    seeds = [client_seed("CL-1", "Ana Ruiz", "111111", "ana@gmail.com", "3001111111")]
    first = Directory(registry)
    first.add_seeds(seeds, {})
    second = Directory(registry)
    second.add_seeds(
        [client_seed("CL-9", "Luis Mora", "999999", "lm@gmail.com", "3009999999"), *seeds], {}
    )
    assert second.owner_of("cliente_id", "CL-1") == first.owner_of("cliente_id", "CL-1")


def test_shared_email_across_corpora_does_not_merge_persons(registry) -> None:
    old = Directory(registry)
    old.add_seeds(
        [client_seed("CL-1", "Manuela Castaño Ruiz", "111111", "mcastano@gmail.com", "3001111111")],
        {},
    )
    manuela = old.owner_of("cliente_id", "CL-1")
    new = Directory(registry)
    new.add_seeds(
        [client_seed("CL-2", "Martina Castaño Díaz", "222222", "mcastano@gmail.com", "3002222222")],
        {},
    )
    martina = new.owner_of("cliente_id", "CL-2")
    assert martina != manuela
    assert new.owner_of("email", "mcastano@gmail.com") == martina
    assert Directory.from_registry(registry).persons[manuela].name.full == "Manuela Castaño Ruiz"


def test_relisted_client_row_joins_the_person_on_cedula_and_contact(registry) -> None:
    d = Directory(registry)
    first = client_seed(
        "CL-1", "Ana Ruiz Gómez", "111111", "ana@gmail.com", "3001111111", vinculacion="2024-01-02"
    )
    relisted = client_seed(
        "PG-1", "Ana Ruiz Gómez", "111111", "ana@gmail.com", "3001111111", vinculacion="2026-10-15"
    )
    d.add_seeds([first, relisted], {})
    assert len(d.persons) == 1
    person = d.owner_of("cliente_id", "CL-1")
    assert d.owner_of("cliente_id", "PG-1") == person
    assert d.persons[person].attrs["vinculacion"] == "2024-01-02"


def test_client_rows_sharing_less_than_cedula_and_contact_stay_apart(registry) -> None:
    d = Directory(registry)
    d.add_seeds(
        [
            client_seed("CL-1", "Ana Ruiz", "111111", "ana@gmail.com", "3001111111"),
            client_seed("CL-2", "Ana Ruiz", "222222", "ana.r@gmail.com", "3002222222"),
            client_seed("CL-3", "Luis Mora", "333333", "ana@gmail.com", "3003333333"),
            client_seed("CL-4", "Luis Mora", "111111", "lmora@gmail.com", "3004444444"),
        ],
        {},
    )
    owners = {d.owner_of("cliente_id", f"CL-{i}") for i in range(1, 5)}
    assert len(owners) == 4


def test_no_person_is_created_without_an_identifier_of_its_own(registry) -> None:
    d = Directory(registry)
    pqr = PersonSeed("pqrs:PQR-1", "pqr_link", "")
    pqr.add("cliente_id", "PG-1")
    pqr.add("cedula", "111111")
    bare_row = PersonSeed("clientes:PG-1", "client", "Ana Ruiz")
    bare_row.add("cliente_id", "PG-1")
    bare_row.add("cedula", "111111")
    d.add_seeds(
        [client_seed("CL-1", "Ana Ruiz", "111111", "ana@gmail.com", "3001111111"), pqr, bare_row],
        {},
    )
    assert len(d.created) == 1
    assert d.owner_of("cliente_id", "PG-1") == d.owner_of("cliente_id", "CL-1")
