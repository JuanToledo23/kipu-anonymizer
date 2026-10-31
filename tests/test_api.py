from pathlib import Path

from fastapi.testclient import TestClient

from kipu.api import create_app
from kipu.config import Settings


def settings(tmp_path: Path, token: str = "") -> Settings:
    return Settings(tmp_path, "secret", "", "", token, 9000)


def test_health_and_missing_output(tmp_path: Path) -> None:
    client = TestClient(create_app(settings(tmp_path)))
    body = client.get("/v1/health").json()
    assert body["status"] == "ok" and body["models"] == []
    assert client.get("/v1/output/chats.jsonl").status_code == 404
    assert client.get("/v1/output/other.txt").status_code == 404


def test_anonymize_validates_schema(tmp_path: Path) -> None:
    client = TestClient(create_app(settings(tmp_path)))
    bad = client.post("/v1/anonymize", json={"source": "chats", "records": [{"id": "x"}]})
    assert bad.status_code == 422
    good = client.post(
        "/v1/anonymize",
        json={
            "source": "chats",
            "records": [
                {
                    "id": "m1",
                    "thread_id": "c:1",
                    "author": "Valeria Gómez",
                    "ts": "1",
                    "text": "Hola, soy Valeria Gómez, mi correo es vgomez@gmail.com",
                    "attachments": [],
                }
            ],
        },
    )
    assert good.status_code == 200
    record = good.json()["records"][0]
    assert "Valeria Gómez" not in record["text"] and "vgomez@gmail.com" not in record["text"]
    assert record["author"] in record["text"]


def test_optional_bearer_auth_with_public_health(tmp_path: Path) -> None:
    client = TestClient(create_app(settings(tmp_path, token="abc")))
    auth = {"Authorization": "Bearer abc"}
    for base in ("/v1", ""):
        assert client.get(f"{base}/health").json()["status"] == "ok"
        assert client.get(f"{base}/entities").status_code == 401
        assert client.get(f"{base}/entities", headers=auth).status_code == 404
        assert client.post(f"{base}/anonymize", json={}).status_code == 401
        assert client.post(f"{base}/anonymize", json={}, headers=auth).status_code == 422


def test_new_person_owns_the_identifiers_next_to_the_name(tmp_path: Path) -> None:
    client = TestClient(create_app(settings(tmp_path)))
    record = {
        "id": "m1",
        "thread_id": "c:1",
        "author": "Kipi",
        "ts": "1",
        "text": "Nueva clienta: Valentina Quiceno, correo vquiceno@gmail.com, cel 3201234567.",
        "attachments": [],
    }
    body = client.post("/v1/anonymize", json={"source": "chats", "records": [record]}).json()
    people = [e for e in body["entities"] if e["type"] == "person"]
    assert len(people) == 1
    assert set(people[0]["pseudonyms"]) == {"name", "email", "phone"}
    assert "Quiceno" not in body["records"][0]["text"]


def test_settings_ignore_surrounding_whitespace(monkeypatch) -> None:
    from kipu import config

    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    monkeypatch.setenv("PSEUDONYM_SECRET", "abc  ")
    monkeypatch.setenv("ENTRYPOINT_TOKEN", " tok ")
    settings = config.load_settings()
    assert settings.pseudonym_secret == "abc" and settings.entrypoint_token == "tok"


def test_cors_preflight_only_on_public_demo_routes(tmp_path: Path) -> None:
    client = TestClient(create_app(settings(tmp_path, token="abc")))
    preflight = {
        "Origin": "https://demo.example",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Content-Type",
    }
    for path in ("/v1/anonymize", "/anonymize", "/v1/health"):
        ok = client.options(path, headers=preflight)
        assert ok.status_code == 200
        assert ok.headers["access-control-allow-origin"] == "*"
        assert "POST" in ok.headers["access-control-allow-methods"]
        assert "content-type" in ok.headers["access-control-allow-headers"].lower()
    health = client.get("/v1/health", headers={"Origin": "https://demo.example"})
    assert health.headers["access-control-allow-origin"] == "*"
    other = client.options("/v1/run", headers=preflight)
    assert "access-control-allow-origin" not in other.headers
