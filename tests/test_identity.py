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
