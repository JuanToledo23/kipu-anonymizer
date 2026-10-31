from conftest import SECRET
from kipu.directory.names import fold
from kipu.pseudo.surrogates import SurrogateFactory, ascii_token
from kipu.replace.render import fit_alnum


def factory(registry, directory) -> SurrogateFactory:
    return SurrogateFactory(registry, SECRET, set(), set(), lambda field, value: False)


def person(directory, key: str):
    return directory.persons[directory.owner_of("cliente_id", key)]


def test_name_is_stable_and_disjoint_from_original(registry, directory) -> None:
    f = factory(registry, directory)
    p = person(directory, "CL-88627")
    name = f.name(p)
    assert f.name(p) == name
    original = {fold(t) for t in ("Manuela", "Castaño", "Ruiz")}
    assert not original & {fold(t) for t in name.given + name.surnames}
    assert len(name.surnames) == 2


def test_names_are_unique_across_persons(registry, directory) -> None:
    f = factory(registry, directory)
    shorts = {f.name(p).short for p in directory.persons.values()}
    assert len(shorts) == len(directory.persons)


def test_same_secret_same_surrogates_in_a_fresh_registry(tmp_path, directory) -> None:
    from kipu.directory.registry import Registry

    p = person(directory, "CL-88627")
    a = SurrogateFactory(Registry(tmp_path / "a.sqlite"), SECRET, set(), set(), lambda *_: False)
    b = SurrogateFactory(Registry(tmp_path / "b.sqlite"), SECRET, set(), set(), lambda *_: False)
    assert a.name(p) == b.name(p)
    assert a.cedula(p, "1107360039") == b.cedula(p, "1107360039")


def test_identifier_shapes(registry, directory) -> None:
    f = factory(registry, directory)
    p = person(directory, "CL-88627")
    cedula = f.cedula(p, "1107360039")
    assert len(cedula) == 10 and cedula[:2] == "11" and cedula != "1107360039"
    phone = f.phone(p, "3148729552")
    assert len(phone) == 10 and phone.startswith("3")
    personal = f.email(p, "mcastano@gmail.com")
    assert personal.split("@")[1] in (
        "gmail.com",
        "outlook.com",
        "yahoo.com",
        "icloud.com",
        "hotmail.com",
    )
    staff = directory.persons[directory.owner_of("kp_id", "KP-016")]
    assert f.email(staff, "kelly.castano@kipupay.co").endswith("@kipupay.co")
    birth = f.birth(p, "1997-07-23")
    assert birth != "1997-07-23" and 1 <= abs(int(birth[:4]) - 1997) <= 3
    assert len(f.plate(p, "LTE758")) == 6 and f.plate(p, "ABC12D")[-1].isalpha()


def test_real_values_are_never_reused(registry, directory) -> None:
    taken = {"1997-07-23"}
    f = SurrogateFactory(registry, SECRET, set(), set(), lambda field, value: value in taken)
    p = person(directory, "CL-88627")
    assert f.birth(p, "1998-01-01") not in taken


def test_surrogate_plate_keeps_its_class(registry, directory) -> None:
    observed = {"JXA971", "GTD596"}
    f = SurrogateFactory(registry, SECRET, set(), set(), lambda field, value: value in observed)
    p = person(directory, "CL-88627")
    car, moto = f.plate(p, "JXA971"), f.plate(p, "ABC12D")
    assert car[:3].isalpha() and car[:3].isupper() and car[3:].isdigit() and len(car) == 6
    assert moto[:3].isalpha() and moto[3:5].isdigit() and moto[5].isalpha()
    assert car not in observed and f.plate(p, "JXA971") == car
    assert fit_alnum("JXA-971", car) == f"{car[:3]}-{car[3:]}"
    assert fit_alnum("ABC 12D", moto) == f"{moto[:3]} {moto[3:]}"


def test_handle_surrogate_follows_the_email_rule(registry, directory) -> None:
    f = factory(registry, directory)
    p = person(directory, "CL-88627")
    local = f.email(p, "mcastano@gmail.com").split("@")[0]
    assert f.handle(p, "mcastano", "mcastano@gmail.com") == local
    given = ascii_token(f.name(p).given[0])
    assert f.handle(p, "manuela.castano").startswith(f"{given}.")
