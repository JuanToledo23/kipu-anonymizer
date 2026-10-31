"""HTTP service per api/participante.openapi.yaml, mounted under /v1 and at the root."""

import datetime as dt
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Literal

import httpx
import orjson
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from starlette.types import ASGIApp, Receive, Scope, Send

from kipu import __version__
from kipu.anonymizer import Anonymizer
from kipu.audit.auditor import record_text
from kipu.config import Settings, load_settings
from kipu.directory.directory import Directory
from kipu.directory.registry import Registry
from kipu.ingest.records import ChatRecord, DocRecord, TableRecord
from kipu.llm.client import LLMClient
from kipu.llm.layer import HookFactory, LLMLayer
from kipu.pipeline import OUTPUT_FILES, http_source, run

log = logging.getLogger("kipu.api")

TEAM = "Juan Toledo"
RECORD_MODELS: dict[str, Any] = {
    "chats": TypeAdapter(list[ChatRecord]),
    "docs": TypeAdapter(list[DocRecord]),
    "tables": TypeAdapter(list[TableRecord]),
}
MEDIA_TYPES = {".jsonl": "application/x-ndjson", ".json": "application/json"}
MAX_JOBS = 200
CORS_PATHS = frozenset({"/health", "/v1/health", "/anonymize", "/v1/anonymize"})


class OrjsonResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return orjson.dumps(content)


class PathCORS:
    """CORS for the public demo page on `CORS_PATHS` only; every other route stays same-origin."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.cors = CORSMiddleware(
            app,
            allow_origins=["*"],
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type"],
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        wanted = scope["type"] == "http" and scope["path"] in CORS_PATHS
        await (self.cors if wanted else self.app)(scope, receive, send)


class RunRequest(BaseModel):
    """Both fields come from the body; `.env` (FUENTES_URL, FUENTES_TOKEN) only fills a gap."""

    sources_url: str | None = None
    token: str | None = None


class AnonymizeRequest(BaseModel):
    source: Literal["chats", "docs", "tables"]
    records: list[dict[str, Any]] = Field(max_length=50)


class Job:
    FIELDS = ("status", "started_at", "finished_at", "records", "cost", "error", "audit_leaks")

    def __init__(self) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.status = "queued"
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.records: dict[str, int] = {}
        self.cost: dict[str, Any] = {"tokens_in": 0, "tokens_out": 0, "seconds": 0.0, "usd": 0.0}
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

    def to_state(self) -> dict[str, Any]:
        return {"id": self.id, **{k: getattr(self, k) for k in self.FIELDS}}

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> "Job":
        job = cls()
        job.id = state["id"]
        for key in cls.FIELDS:
            if key in state:
                setattr(job, key, state[key])
        return job


class JobManager:
    """Runs one regeneration at a time in a background thread. The job list lives in
    `$DATA_DIR/jobs.json` so `GET /run/{id}` still answers after a restart."""

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport
        self.path = settings.data_dir / "jobs.json"
        self.lock = threading.Lock()
        self.jobs: dict[str, Job] = self._load()
        self.current: Job | None = None

    def _load(self) -> dict[str, Job]:
        if not self.path.is_file():
            return {}
        try:
            states = orjson.loads(self.path.read_bytes())
        except orjson.JSONDecodeError:
            log.warning("unreadable %s, starting with an empty job list", self.path)
            return {}
        jobs = {s["id"]: Job.from_state(s) for s in states}
        interrupted = [j for j in jobs.values() if j.status in ("queued", "running")]
        for job in interrupted:
            job.status = "failed"
            job.error = "interrupted: the service restarted before the run finished"
            job.finished_at = job.finished_at or _now()
        if interrupted:
            _write_json(self.path, [j.to_state() for j in jobs.values()])
        return jobs

    def save(self) -> None:
        with self.lock:
            states = [j.to_state() for j in list(self.jobs.values())[-MAX_JOBS:]]
            _write_json(self.path, states)

    def submit(self, request: RunRequest) -> Job:
        url = request.sources_url or self.settings.fuentes_url
        token = request.token or self.settings.fuentes_token
        with self.lock:
            if self.current and self.current.status in ("queued", "running"):
                return self.current
            job = Job()
            self.jobs[job.id] = job
            self.current = job
        self.save()
        threading.Thread(target=self._run, args=(job, url, token), daemon=True).start()
        return job

    def _run(self, job: Job, url: str, token: str) -> None:
        """Any exception marks the job failed; output/ is only replaced by a finished run."""
        try:
            job.status = "running"
            job.started_at = _now()
            self.save()
            if not url or not token:
                raise ValueError("sources_url and token are required (body or .env)")
            source = http_source(url, token, self.settings, job.id, self.transport)
            try:
                report = run(source, self.settings.output_dir, self.settings, job.id)
            finally:
                source.close()
            job.records = report.records
            job.cost = {
                "tokens_in": report.tokens_in,
                "tokens_out": report.tokens_out,
                "seconds": report.seconds,
                "usd": report.usd,
                "models": report.llm.get("models", {}),
            }
            job.audit_leaks = len(report.audit_hits)
            job.status = "done"
        except Exception as exc:
            log.exception("run %s failed", job.id)
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.finished_at = _now()
            self.save()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(orjson.dumps(value, option=orjson.OPT_INDENT_2))
    os.replace(tmp, path)


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


class AnonymizeService:
    """Live anonymization against the persistent registry (same pseudonyms as output/).

    Batches run one at a time: each one reads the registry, may add persons, and the next
    batch must see them so a new person gets one pseudonym. A run in progress uses its own
    connection; the registry's process-wide write lock keeps the two consistent."""

    def __init__(self, settings: Settings, client: LLMClient | None = None) -> None:
        """`client` replaces the LLM client (tests); by default a fresh one per batch."""
        self.settings = settings
        self.lock = threading.Lock()
        self.client = client

    def anonymize(self, source: str, records: list[Any]) -> dict[str, Any]:
        """Rules and registry first; with the LLM layer on, the primary model then adds what it
        verifies within the time budget (`LLMSettings.anonymize_budget`)."""
        with self.lock:
            registry = Registry(self.settings.registry_path)
            try:

                def run_pass(
                    hook: HookFactory | None, sectors: dict[str, str]
                ) -> tuple[Anonymizer, dict[str, list[dict[str, Any]]]]:
                    anonymizer = Anonymizer(
                        registry,
                        Directory.from_registry(registry),
                        self.settings.pseudonym_secret,
                        live=True,
                        sectors=sectors,
                    )
                    if hook is not None:
                        anonymizer.detector.extra = hook(anonymizer)
                    anonymizer.observe(record_text(r.model_dump()) for r in records)
                    return anonymizer, {source: getattr(anonymizer, source)(records)}

                if self.settings.llm.enabled:
                    layer = LLMLayer(self.settings.llm, self.settings.llm_cache_dir, self.client)
                    anonymizer, outputs = layer.refine_batch(source, records, run_pass)
                else:
                    anonymizer, outputs = run_pass(None, {})
                return {
                    "records": outputs[source],
                    "entities": anonymizer.entities(outputs[source]),
                }
            finally:
                registry.close()


def create_app(
    settings: Settings | None = None,
    transport: httpx.BaseTransport | None = None,
    llm_client: LLMClient | None = None,
) -> FastAPI:
    """`transport` replaces the network for the sources API and `llm_client` the LLM
    providers (tests)."""
    settings = settings or load_settings()
    jobs = JobManager(settings, transport)
    anonymizer = AnonymizeService(settings, llm_client)
    app = FastAPI(title="Kipu Pay anonymizer", default_response_class=OrjsonResponse)
    app.add_middleware(PathCORS)

    def authorize(authorization: str | None = Header(default=None)) -> None:
        expected = settings.entrypoint_token
        if expected and authorization != f"Bearer {expected}":
            raise HTTPException(status_code=401, detail="unauthorized")

    public = APIRouter()
    router = APIRouter(dependencies=[Depends(authorize)])

    @public.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "team": TEAM,
            "version": __version__,
            "models": settings.llm.models,
        }

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

    # The platform health check and the judge may use either base path; /health exposes no secrets.
    for prefix in ("/v1", ""):
        app.include_router(public, prefix=prefix)
        app.include_router(router, prefix=prefix)
    return app


def app_factory() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return create_app()
