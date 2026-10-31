"""HTTP service per api/participante.openapi.yaml, mounted under /v1."""

import datetime as dt
import logging
import threading
import uuid
from typing import Any, Literal

import orjson
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from kipu import __version__
from kipu.anonymizer import Anonymizer
from kipu.config import Settings, load_settings
from kipu.directory.directory import Directory
from kipu.directory.registry import Registry
from kipu.ingest.records import ChatRecord, DocRecord, TableRecord
from kipu.pipeline import OUTPUT_FILES, http_source, run

log = logging.getLogger("kipu.api")

TEAM = "Juan Toledo"
RECORD_MODELS: dict[str, Any] = {
    "chats": TypeAdapter(list[ChatRecord]),
    "docs": TypeAdapter(list[DocRecord]),
    "tables": TypeAdapter(list[TableRecord]),
}
MEDIA_TYPES = {".jsonl": "application/x-ndjson", ".json": "application/json"}


class OrjsonResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return orjson.dumps(content)


class RunRequest(BaseModel):
    sources_url: str
    token: str


class AnonymizeRequest(BaseModel):
    source: Literal["chats", "docs", "tables"]
    records: list[dict[str, Any]] = Field(max_length=50)


class Job:
    def __init__(self) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.status = "queued"
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.records: dict[str, int] = {}
        self.cost = {"tokens_in": 0, "tokens_out": 0, "seconds": 0.0}
        self.error: str | None = None
        self.audit_leaks = 0

    def as_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "job_id": self.id,
            "status": self.status,
            "records": self.records,
            "cost": self.cost,
            "audit_leaked_records": self.audit_leaks,
        }
        for key in ("started_at", "finished_at", "error"):
            if getattr(self, key):
                body[key] = getattr(self, key)
        return body


class JobManager:
    """Runs one regeneration at a time in a background thread."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.jobs: dict[str, Job] = {}
        self.current: Job | None = None
        self.lock = threading.Lock()

    def submit(self, request: RunRequest) -> Job:
        with self.lock:
            if self.current and self.current.status in ("queued", "running"):
                return self.current
            job = Job()
            self.jobs[job.id] = job
            self.current = job
        threading.Thread(target=self._run, args=(job, request), daemon=True).start()
        return job

    def _run(self, job: Job, request: RunRequest) -> None:
        job.status = "running"
        job.started_at = _now()
        source = http_source(request.sources_url, request.token, self.settings, job.id)
        try:
            report = run(source, self.settings.output_dir, self.settings)
            job.records = report.records
            job.cost = {
                "tokens_in": report.tokens_in,
                "tokens_out": report.tokens_out,
                "seconds": report.seconds,
            }
            job.audit_leaks = len(report.audit_hits)
            job.status = "done"
        except Exception as exc:
            log.exception("run %s failed", job.id)
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            source.close()
            job.finished_at = _now()


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


class AnonymizeService:
    """Live anonymization against the persistent registry (same pseudonyms as output/)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.lock = threading.Lock()

    def anonymize(self, source: str, records: list[Any]) -> dict[str, Any]:
        with self.lock:
            registry = Registry(self.settings.registry_path)
            try:
                anonymizer = Anonymizer(
                    registry, Directory.from_registry(registry), self.settings.pseudonym_secret
                )
                outputs = getattr(anonymizer, source)(records)
                return {"records": outputs, "entities": anonymizer.entities(outputs)}
            finally:
                registry.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    jobs = JobManager(settings)
    anonymizer = AnonymizeService(settings)
    app = FastAPI(title="Kipu Pay anonymizer", default_response_class=OrjsonResponse)

    def authorize(authorization: str | None = Header(default=None)) -> None:
        expected = settings.entrypoint_token
        if expected and authorization != f"Bearer {expected}":
            raise HTTPException(status_code=401, detail="unauthorized")

    router = APIRouter(prefix="/v1", dependencies=[Depends(authorize)])

    @router.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "team": TEAM, "version": __version__, "models": []}

    @router.post("/run", status_code=202)
    def start_run(request: RunRequest) -> dict[str, str]:
        return {"job_id": jobs.submit(request).id}

    @router.get("/run/{job_id}")
    def run_status(job_id: str) -> dict[str, Any]:
        job = jobs.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job")
        return job.as_dict()

    @router.get("/output/{name}")
    def output_file(name: str) -> FileResponse:
        path = settings.output_dir / name
        if name not in OUTPUT_FILES or not path.is_file():
            raise HTTPException(status_code=404, detail="no completed run")
        return FileResponse(path, media_type=MEDIA_TYPES[path.suffix])

    @router.get("/entities")
    def entities() -> FileResponse:
        return output_file("entities.json")

    @router.post("/anonymize")
    def anonymize(request: AnonymizeRequest) -> dict[str, Any]:
        try:
            records = RECORD_MODELS[request.source].validate_python(request.records)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422, detail=exc.errors(include_url=False, include_context=False)
            ) from exc
        return anonymizer.anonymize(request.source, records)

    app.include_router(router)
    return app


def app_factory() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return create_app()
