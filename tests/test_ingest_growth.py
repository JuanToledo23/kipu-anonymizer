"""Sources growth: dynamic enumeration, resilient client, and stable pseudonyms across runs."""

import csv
import io
import json
import logging
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from tests.fake_sources import FakeSources

from kipu.config import Settings
from kipu.directory.registry import Registry
from kipu.directory.seed import norm_identifier
from kipu.ingest import source as source_module
from kipu.ingest.source import DirSource, HttpSource, SourceError, SourceNotFound
from kipu.pipeline import run

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "sample" / "input"
# Pinned snapshots: run 1's corpus and the grown one (Págalo migration), never "the newest".
OLD_RAW = ROOT / "data" / "raw" / "5e3e6756311e"
GROWN_RAW = ROOT / "data" / "raw" / "growth_20261031T215550"
URL = "http://sources.test/v1"


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = SimpleNamespace(monotonic=source_module.time.monotonic, sleep=lambda _: None)
    monkeypatch.setattr(source_module, "time", clock)


def http(fake: FakeSources, raw: Path, **kwargs: Any) -> HttpSource:
    return HttpSource(URL, fake.token, raw, transport=httpx.MockTransport(fake), **kwargs)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def message(msg_id: str, user: str, text: str, ts: str) -> dict[str, str]:
    return {"type": "message", "user": user, "text": text, "ts": ts, "client_msg_id": msg_id}


def output_lines(out: Path) -> dict[str, bytes]:
    lines = {}
    for name in ("chats", "docs", "tables"):
        for line in (out / f"{name}.jsonl").read_bytes().splitlines():
            lines[json.loads(line)["id"]] = line
    return lines


def entities(out: Path) -> dict[str, dict[str, Any]]:
    return {e["entity_id"]: e for e in read_json(out / "entities.json")["entities"]}


def test_http_source_has_connect_and_read_timeouts(tmp_path: Path) -> None:
    source = http(FakeSources(tmp_path), tmp_path / "raw")
    assert source.client.timeout.connect == 10.0 and source.client.timeout.read == 60.0


def test_http_source_gives_up_with_a_clear_error_and_never_retries_404(
    tmp_path: Path, no_sleep: None
) -> None:
    fake = FakeSources(SAMPLE)
    fake.faults["/v1/chats/users"] = [503] * 10
    source = http(fake, tmp_path / "raw", max_retries=2)
    with pytest.raises(SourceError, match=r"chats/users: HTTP 503"):
        source.users()
    assert fake.hits("/v1/chats/users") == 3
    with pytest.raises(SourceNotFound):
        source.messages("soporte-cobros", "1999-01-01")
    assert fake.hits("/v1/chats/soporte-cobros/1999-01-01") == 1


def test_http_source_refetches_when_a_304_has_no_cached_copy(tmp_path: Path) -> None:
    calls: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("if-none-match"))
        if request.headers.get("if-none-match"):
            return httpx.Response(304)
        return httpx.Response(200, json=[{"name": "x"}], headers={"etag": '"v1"'})

    etags = tmp_path / "etags.json"
    write_json(etags, {f"{URL}/chats/channels": {"etag": '"v1"', "file": "/gone.json"}})
    source = HttpSource(
        URL, "t", tmp_path / "raw", etag_cache=etags, transport=httpx.MockTransport(handler)
    )
    assert source.channels() == [{"name": "x"}]
    assert calls == ['"v1"', None]


def grown_sample(tmp_path: Path) -> Path:
    """The sample plus a new channel, a new date, a new doc, new rows and an unknown table."""
    root = tmp_path / "grown"
    shutil.copytree(SAMPLE, root)
    channels = read_json(root / "chats" / "channels.json")
    channels.append({**channels[0], "id": "C0NEW0001", "name": "soporte-nuevo"})
    write_json(root / "chats" / "channels.json", channels)
    user = read_json(root / "chats" / "users.json")[1]["id"]
    write_json(
        root / "chats" / "soporte-nuevo" / "2026-11-02.json",
        [message("new-channel-1", user, "Canal nuevo, primer mensaje.", "1793000000.000100")],
    )
    write_json(
        root / "chats" / "soporte-cobros" / "2026-11-03.json",
        [message("new-date-1", user, "Una fecha nueva en un canal viejo.", "1793100000.000100")],
    )
    docs = read_json(root / "docs" / "index.json")
    docs.append({"id": "sdoc_99", "filename": "memo_nuevo.md", "format": "md"})
    write_json(root / "docs" / "index.json", docs)
    (root / "docs" / "memo_nuevo.md").write_text("# Memo nuevo\n\nSin datos personales.\n")
    with (root / "tables" / "clientes.csv").open("ab") as fh:
        fh.write(
            "CL-99999;Rubiela Patiño Gaviria;CC;52999888;rpatino@gmail.com;3209998877;Pasto;"
            "Centro;Calle 18 # 25-30;1975-03-02;2026-11-01;docente;\n".encode("latin-1")
        )
    (root / "tables" / "beneficiarios.csv").write_text("id,nombre\nBEN-1,Sin nombre\n")
    return root


@pytest.mark.skipif(not SAMPLE.is_dir(), reason="sample data not downloaded")
def test_new_channels_dates_docs_and_tables_are_enumerated(tmp_path: Path) -> None:
    settings = Settings(tmp_path / "data", "secret", "", "", "", 9000)
    report = run(DirSource(grown_sample(tmp_path)), tmp_path / "out", settings)
    ids = set(output_lines(tmp_path / "out"))
    assert {"new-channel-1", "new-date-1", "sdoc_99", "clientes:CL-99999"} <= ids
    assert "beneficiarios:BEN-1" not in ids  # outside the output schema's four tables
    reference = run(DirSource(SAMPLE), tmp_path / "ref", settings)
    assert report.records["chats"] == reference.records["chats"] + 2
    assert report.records["docs"] == reference.records["docs"] + 1
    assert report.records["tables"] == reference.records["tables"] + 1


@pytest.mark.skipif(not SAMPLE.is_dir(), reason="sample data not downloaded")
def test_missing_and_empty_listed_resources_are_skipped_with_a_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = grown_sample(tmp_path)
    channels = read_json(root / "chats" / "channels.json")
    write_json(root / "chats" / "channels.json", [*channels, {"name": "sin-fechas"}])
    dates = [*DirSource(root).channel_dates("soporte-cobros"), "2026-12-01", "2026-12-02"]
    write_json(root / "chats" / "soporte-cobros" / "_dates.json", dates)
    write_json(root / "chats" / "soporte-cobros" / "2026-12-02.json", [])
    docs = read_json(root / "docs" / "index.json")
    write_json(
        root / "docs" / "index.json", [*docs, {"id": "x", "filename": "x.md", "format": "md"}]
    )

    settings = Settings(tmp_path / "data", "secret", "", "", "", 9000)
    with caplog.at_level(logging.INFO):
        report = run(http(FakeSources(root), tmp_path / "raw"), tmp_path / "out", settings)
    assert report.published
    assert {"new-channel-1", "new-date-1", "sdoc_99"} <= set(output_lines(tmp_path / "out"))
    logged = caplog.text
    assert "chats/sin-fechas: no dates listing, channel skipped" in logged
    assert "chats/soporte-cobros/2026-12-01: listed but not found, skipped" in logged
    assert "chats/soporte-cobros/2026-12-02: empty, skipped" in logged
    assert "doc x (x.md) listed but not found, skipped" in logged
    assert "table beneficiarios.csv is not in the output schema, skipped" in logged


def known_client_user(raw: Path) -> tuple[dict[str, Any], dict[str, str]]:
    """A Slack client whose real name matches exactly one clientes row."""
    data = (raw / "tables" / "clientes.csv").read_bytes().decode("latin-1")
    rows = list(csv.DictReader(io.StringIO(data), delimiter=";"))
    for user in read_json(raw / "chats" / "users.json"):
        if user.get("profile", {}).get("title") != "Cliente":
            continue
        matches = [r for r in rows if r["nombre_completo"] == user.get("real_name")]
        if len(matches) == 1:
            return user, matches[0]
    raise AssertionError("no client user with a unique master row")


def unused_number(raw: Path, start: int) -> str:
    corpus = "".join(p.read_text("latin-1") for p in raw.rglob("*") if p.is_file())
    value = start
    while str(value) in corpus:
        value += 1
    return str(value)


@pytest.mark.parametrize("raw", [OLD_RAW, GROWN_RAW], ids=["run1", "grown"])
def test_growth_keeps_old_persons_and_pseudonyms(tmp_path: Path, raw: Path) -> None:
    if not (raw / "chats" / "users.json").is_file():
        pytest.skip(f"{raw.name} is not under data/raw")
    user, row = known_client_user(raw)
    new_phone = unused_number(raw, 3157778800)
    new_cedula = unused_number(raw, 1020304050)
    brand_new_cedula = unused_number(raw, 79555100)
    grown = tmp_path / "grown"
    shutil.copytree(raw, grown)
    users = read_json(grown / "chats" / "users.json")
    homonym = {
        "id": "U0GROWB01",
        "name": "homonimo",
        "real_name": row["nombre_completo"],
        "is_bot": False,
        "profile": {"real_name": row["nombre_completo"], "display_name": "", "title": "Cliente"},
    }
    stranger_name = "Gilberto Arciniegas Pabón"
    stranger = {
        "id": "U0GROWC01",
        "name": "garciniegas",
        "real_name": stranger_name,
        "is_bot": False,
        "profile": {"real_name": stranger_name, "display_name": "", "title": "Cliente"},
    }
    write_json(grown / "chats" / "users.json", [*users, homonym, stranger])
    channel = grown / "chats" / "soporte-cobros"
    day = [
        message(
            "grow-a",
            user["id"],
            f"Hola, cambié de número. Mi nuevo celular es {new_phone}, por favor actualícenlo.",
            "1792500000.000100",
        ),
        message(
            "grow-b",
            homonym["id"],
            f"Buenas, soy {row['nombre_completo']}, cédula {new_cedula}. Quiero abrir una cuenta.",
            "1792500100.000100",
        ),
        message(
            "grow-c",
            stranger["id"],
            f"Hola, soy {stranger_name}, correo garciniegas@gmail.com, cédula {brand_new_cedula}.",
            "1792500200.000100",
        ),
    ]
    write_json(channel / "2026-12-20.json", day)
    if (channel / "_dates.json").is_file():
        write_json(channel / "_dates.json", [*read_json(channel / "_dates.json"), "2026-12-20"])

    settings = Settings(tmp_path / "data", "secret", "", "", "", 9000)
    run(DirSource(raw), tmp_path / "before", settings)
    registry = Registry(settings.registry_path)
    client_id = registry.owner("cliente_id", norm_identifier("cliente_id", row["cliente_id"]))
    surrogates_before = set(registry.conn.execute("SELECT * FROM surrogates").fetchall())
    registry.close()
    report = run(DirSource(grown), tmp_path / "after", settings)

    before, after = entities(tmp_path / "before"), entities(tmp_path / "after")
    changed = [eid for eid, e in before.items() if after[eid]["pseudonyms"] != e["pseudonyms"]]
    assert changed == []
    old_lines, new_lines = output_lines(tmp_path / "before"), output_lines(tmp_path / "after")
    assert [rid for rid, line in old_lines.items() if new_lines[rid] != line] == []

    registry = Registry(settings.registry_path)
    try:
        assert (
            registry.owner("cliente_id", norm_identifier("cliente_id", row["cliente_id"]))
            == client_id
        )
        assert registry.owner("phone", new_phone) == client_id  # (a) same person, phone added
        homonym_id = registry.owner("cedula", new_cedula)
        stranger_id = registry.owner("cedula", brand_new_cedula)
        assert surrogates_before <= set(registry.conn.execute("SELECT * FROM surrogates"))
    finally:
        registry.close()
    assert client_id in before and "grow-a" in after[client_id]["records"]
    assert new_phone in after[client_id]["originals"]
    for new_id in (homonym_id, stranger_id):  # (b) and (c): new persons, new pseudonyms
        assert new_id and new_id not in before and new_id != client_id
    assert after[homonym_id]["pseudonyms"]["name"] != after[client_id]["pseudonyms"]["name"]
    assert "grow-b" in after[homonym_id]["records"]
    assert "grow-c" in after[stranger_id]["records"]
    assert report.persons_created == 2
    for record_id in ("grow-a", "grow-b", "grow-c"):
        text = json.loads(new_lines[record_id])["text"]
        for original in (new_phone, new_cedula, brand_new_cedula, row["nombre_completo"]):
            assert original not in text
