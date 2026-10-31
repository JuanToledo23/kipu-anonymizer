"""/anonymize with the LLM layer: the time budget holds with a slow provider."""

import time
from pathlib import Path

from fastapi.testclient import TestClient
from tests.llm_fakes import LLM, PRIMARY, SECOND, STRONG, FakeProviders, extraction

from kipu.api import create_app
from kipu.config import LLMSettings, Settings
from kipu.llm.client import LLMClient


def test_anonymize_answers_within_the_time_budget(tmp_path: Path) -> None:
    llm = LLMSettings(**{**LLM.__dict__, "anonymize_budget": 0.5, "concurrency": 12})
    slow = FakeProviders({PRIMARY: extraction}, delay=60)
    settings = Settings(tmp_path, "secret", "", "", "", 9000, llm)
    app = create_app(settings, llm_client=LLMClient(llm, tmp_path / "c", providers=slow))
    http = TestClient(app)
    assert http.get("/v1/health").json()["models"] == [PRIMARY, SECOND, STRONG]
    records = [
        {
            "id": f"m{i}",
            "thread_id": f"t{i // 12}",
            "author": "Valeria Gómez",
            "ts": str(i),
            "text": f"Soy Valeria Gómez, correo vgomez{i}@gmail.com. Mi cuñado Yeferson Mosquera.",
            "attachments": [],
        }
        for i in range(50)
    ]
    started = time.monotonic()
    response = http.post("/v1/anonymize", json={"source": "chats", "records": records})
    elapsed = time.monotonic() - started
    assert response.status_code == 200 and elapsed < 10
    texts = [r["text"] for r in response.json()["records"]]
    assert all("Valeria Gómez" not in t and "vgomez" not in t for t in texts)
    assert llm.anonymize_budget < LLMSettings().anonymize_budget <= 20
