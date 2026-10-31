"""Service robustness: /run inputs, restarts, concurrent /anonymize, failed jobs, flaky sources."""

import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import orjson
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.fake_sources import FakeSources

from kipu import api
from kipu.config import Settings
from kipu.directory.registry import Registry
from kipu.ingest import source as source_module

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample" / "input"
BODY_URL = "http://body-sources.test/v1"
OUTPUT_FILES = ("chats.jsonl", "docs.jsonl", "tables.jsonl", "entities.json")

pytestmark = pytest.mark.skipif(not SAMPLE.is_dir(), reason="sample data not downloaded")


def settings(data_dir: Path, url: str = "", token: str = "") -> Settings:
    return Settings(data_dir, "secret", url, token, "", 9000)


def wait_job(client: TestClient, job_id: str, timeout: float = 60) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/v1/run/{job_id}").json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} still {body['status']}")


def run_job(client: TestClient, body: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/v1/run", json=body)
    assert response.status_code == 202
    return wait_job(client, response.json()["job_id"])


def outputs(data_dir: Path) -> dict[str, bytes]:
    return {name: (data_dir / "output" / name).read_bytes() for name in OUTPUT_FILES}


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Retries and backoff run instantly; the recorded delays are returned."""
    slept: list[float] = []
    clock = SimpleNamespace(monotonic=time.monotonic, sleep=slept.append)
    monkeypatch.setattr(source_module, "time", clock)
    return slept


def chat(record_id: str, text: str) -> dict[str, Any]:
    return {
        "id": record_id,
        "thread_id": f"c:{record_id}",
        "author": "Kipi",
        "ts": "1",
        "text": text,
        "attachments": [],
    }


NEW_PERSONS = [
    ("Valentina Quiceno Arbeláez", "1088123456", "vquiceno@gmail.com"),
    ("Rodrigo Albarracín Peña", "79123456", "ralbarracin@hotmail.com"),
    ("Marisol Cuartas Jaramillo", "43123987", "mcuartas@yahoo.com"),
    ("Hernando Bermúdez Ossa", "70111222", "hbermudez@outlook.com"),
]


def new_person_record(record_id: str, person: tuple[str, str, str]) -> dict[str, Any]:
    name, cedula, email = person
    return chat(record_id, f"Nueva clienta: {name}, cédula {cedula}, correo {email}.")


def name_pseudonym(body: dict[str, Any], name: str) -> str:
    [entity] = [e for e in body["entities"] if name in e["originals"]]
    return entity["pseudonyms"]["name"]


def registry_names(data_dir: Path) -> list[str]:
    registry = Registry(data_dir / "registry.sqlite")
    try:
        return [p["name"] for p in registry.persons()]
    finally:
        registry.close()


@contextmanager
def live_server(app: FastAPI) -> Iterator[str]:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_run_uses_sources_url_and_token_from_the_body(tmp_path: Path) -> None:
    fake = FakeSources(SAMPLE, token="body-token")
    app = api.create_app(
        settings(tmp_path, "http://env-sources.test/v1", "env-token"), httpx.MockTransport(fake)
    )
    job = run_job(TestClient(app), {"sources_url": BODY_URL, "token": "body-token"})
    assert job["status"] == "done", job
    assert {r.url.host for r in fake.requests} == {"body-sources.test"}


def test_run_falls_back_to_env_only_when_the_body_has_no_sources(tmp_path: Path) -> None:
    fake = FakeSources(SAMPLE, token="env-token")
    app = api.create_app(
        settings(tmp_path, "http://env-sources.test/v1", "env-token"), httpx.MockTransport(fake)
    )
    job = run_job(TestClient(app), {})
    assert job["status"] == "done", job
    assert {r.url.host for r in fake.requests} == {"env-sources.test"}


def test_unreachable_sources_url_fails_the_job_and_keeps_the_previous_output(
    tmp_path: Path, no_sleep: list[float]
) -> None:
    fake = FakeSources(SAMPLE)
    good = TestClient(api.create_app(settings(tmp_path), httpx.MockTransport(fake)))
    assert run_job(good, {"sources_url": BODY_URL, "token": "tok"})["status"] == "done"
    before = outputs(tmp_path)

    client = TestClient(api.create_app(settings(tmp_path, BODY_URL, "tok")))  # real network
    job = run_job(client, {"sources_url": "http://127.0.0.1:9/v1", "token": "tok"})
    assert job["status"] == "failed"
    assert "SourceError" in job["error"] and "127.0.0.1:9" in job["error"]
    assert "tok" not in job["error"].replace("127.0.0.1", "")
    assert outputs(tmp_path) == before
    assert client.get("/v1/output/chats.jsonl").content == before["chats.jsonl"]


def test_output_entities_and_jobs_survive_a_restart(tmp_path: Path) -> None:
    transport = httpx.MockTransport(FakeSources(SAMPLE))
    first = TestClient(api.create_app(settings(tmp_path), transport))
    job = run_job(first, {"sources_url": BODY_URL, "token": "tok"})
    files = {name: first.get(f"/v1/output/{name}").content for name in OUTPUT_FILES}
    entities = first.get("/v1/entities").content
    pseudonym = name_pseudonym(
        first.post(
            "/v1/anonymize",
            json={"source": "chats", "records": [new_person_record("m1", NEW_PERSONS[0])]},
        ).json(),
        NEW_PERSONS[0][0],
    )

    restarted = TestClient(api.create_app(settings(tmp_path), transport))
    assert restarted.get(f"/v1/run/{job['job_id']}").json() == job
    assert {n: restarted.get(f"/v1/output/{n}").content for n in OUTPUT_FILES} == files
    assert restarted.get("/v1/entities").content == entities
    again = restarted.post(
        "/v1/anonymize",
        json={"source": "chats", "records": [new_person_record("m2", NEW_PERSONS[0])]},
    ).json()
    assert name_pseudonym(again, NEW_PERSONS[0][0]) == pseudonym


def test_a_run_interrupted_by_a_restart_is_reported_failed(tmp_path: Path) -> None:
    state = [{"id": "abc123", "status": "running", "started_at": "2026-10-31T00:00:00+00:00"}]
    (tmp_path / "jobs.json").write_bytes(orjson.dumps(state))
    client = TestClient(api.create_app(settings(tmp_path)))
    body = client.get("/v1/run/abc123").json()
    assert body["status"] == "failed" and "restarted" in body["error"]


def test_parallel_anonymize_batches_share_new_persons(tmp_path: Path) -> None:
    app = api.create_app(settings(tmp_path), httpx.MockTransport(FakeSources(SAMPLE)))
    run_job(TestClient(app), {"sources_url": BODY_URL, "token": "tok"})
    batches = [
        [
            new_person_record(f"b{i}a", NEW_PERSONS[i % 4]),
            new_person_record(f"b{i}b", NEW_PERSONS[(i + 1) % 4]),
        ]
        for i in range(10)
    ]
    with live_server(app) as base, ThreadPoolExecutor(10) as pool:
        responses = list(
            pool.map(
                lambda records: httpx.post(
                    f"{base}/v1/anonymize",
                    json={"source": "chats", "records": records},
                    timeout=60,
                ),
                batches,
            )
        )
    assert [r.status_code for r in responses] == [200] * 10
    names = registry_names(tmp_path)
    for name, _, _ in NEW_PERSONS:
        assert names.count(name) == 1, name
        pseudonyms = {
            name_pseudonym(r.json(), name)
            for r in responses
            if any(name in e["originals"] for e in r.json()["entities"])
        }
        assert len(pseudonyms) == 1, (name, pseudonyms)


def test_anonymize_answers_during_a_run_and_the_run_keeps_its_persons(tmp_path: Path) -> None:
    fake = FakeSources(SAMPLE)
    client = TestClient(api.create_app(settings(tmp_path), httpx.MockTransport(fake)))
    run_job(client, {"sources_url": BODY_URL, "token": "tok"})

    def anonymize(record_id: str, person: tuple[str, str, str]) -> str:
        response = client.post(
            "/v1/anonymize",
            json={"source": "chats", "records": [new_person_record(record_id, person)]},
        )
        assert response.status_code == 200
        return name_pseudonym(response.json(), person[0])

    fake.hold("/v1/docs")
    job_id = client.post("/v1/run", json={"sources_url": BODY_URL, "token": "tok"}).json()["job_id"]
    assert fake.reached_gate.wait(timeout=30)
    assert client.get(f"/v1/run/{job_id}").json()["status"] == "running"
    held = anonymize("d1", NEW_PERSONS[1])

    racing: list[str] = []
    stop = threading.Event()

    def keep_anonymizing() -> None:
        i = 0
        while not stop.is_set():
            racing.append(anonymize(f"r{i}", NEW_PERSONS[2]))
            i += 1

    racer = threading.Thread(target=keep_anonymizing)
    racer.start()
    fake.release()
    job = wait_job(client, job_id)
    stop.set()
    racer.join(timeout=30)

    assert job["status"] == "done", job
    assert racing and len(set(racing)) == 1
    names = registry_names(tmp_path)
    assert names.count(NEW_PERSONS[1][0]) == 1 and names.count(NEW_PERSONS[2][0]) == 1
    assert anonymize("d2", NEW_PERSONS[1]) == held
    assert anonymize("d3", NEW_PERSONS[2]) == racing[0]


def test_unexpected_pipeline_error_fails_the_job_and_keeps_the_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(
        api.create_app(settings(tmp_path), httpx.MockTransport(FakeSources(SAMPLE)))
    )
    run_job(client, {"sources_url": BODY_URL, "token": "tok"})
    before = outputs(tmp_path)

    def broken_run(*args: Any, **kwargs: Any) -> None:
        raise KeyError("injected failure")

    monkeypatch.setattr(api, "run", broken_run)
    job = run_job(client, {"sources_url": BODY_URL, "token": "tok"})
    assert job["status"] == "failed" and "injected failure" in job["error"]
    assert job["finished_at"]
    assert outputs(tmp_path) == before

    def broken_source(*args: Any, **kwargs: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(api, "http_source", broken_source)
    job = run_job(client, {"sources_url": BODY_URL, "token": "tok"})
    assert job["status"] == "failed" and "disk full" in job["error"]
    assert client.get("/v1/entities").content == before["entities.json"]


def test_transient_source_errors_do_not_fail_the_run(tmp_path: Path, no_sleep: list[float]) -> None:
    reference = FakeSources(SAMPLE)
    clean = TestClient(api.create_app(settings(tmp_path / "clean"), httpx.MockTransport(reference)))
    expected = run_job(clean, {"sources_url": BODY_URL, "token": "tok"})["records"]

    fake = FakeSources(SAMPLE)
    first_day = "/v1/chats/soporte-cobros/" + fake.source.channel_dates("soporte-cobros")[0]
    fake.faults[first_day] = [500, 502]
    fake.faults["/v1/docs"] = [429]
    client = TestClient(api.create_app(settings(tmp_path / "flaky"), httpx.MockTransport(fake)))
    job = run_job(client, {"sources_url": BODY_URL, "token": "tok"})
    assert job["status"] == "done", job
    assert job["records"] == expected
    assert fake.hits(first_day) == 3 and fake.hits("/v1/docs") == 2
    assert {0.5, 1.0} <= set(no_sleep)  # exponential backoff between attempts
