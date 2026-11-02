# CLAUDE.md

Kipu Pay pseudonymization service for the Frontier challenge "Vende los datos sin vender a la gente".
It regenerates an anonymized corpus (chats, docs, tables + `entities.json`) from the sources API and
serves it over HTTP (`api/participante.openapi.yaml`). Rules first; an optional LLM layer on top
(models propose exact strings, code verifies, replaces and audits).

## Stack
Python 3.13, uv, FastAPI + uvicorn, httpx, pydantic v2, PyMuPDF, lxml, regex, orjson, sqlite3,
openai + anthropic SDKs (OpenRouter), pytest, ruff. Node ≥ 20 for `validar.mjs`.

## Layout
- `src/kipu/ingest/` source clients (`HttpSource`, `DirSource`) and parsers → normalized records
- `src/kipu/directory/` persons from clientes/empleados/users, SQLite registry (`registry.py`)
- `src/kipu/identity/` union-find resolution with cannot-link; homonym evidence (`homonyms.py`)
- `src/kipu/detect/` rule detectors → `Mention`s (`patterns.py`, `names.py`, `engine.py`, `quasi.py`, gazetteer)
- `src/kipu/llm/` LLM layer: cached client, extraction, verification, identity, omissions, generalization
- `src/kipu/pseudo/` deterministic surrogates (HMAC seed + reservation), `names_co.py`
- `src/kipu/replace/` form-preserving rendering, generalization (occupation, barrio)
- `src/kipu/audit/` `texto.py` (port of validar.mjs `norm`/`contiene`), leak auditor
- `src/kipu/anonymizer.py` detect → replace for records (shared by pipeline and API)
- `src/kipu/pipeline.py` CLI run; `src/kipu/api.py` FastAPI app under `/v1`; `src/kipu/entities.py`
- `scripts/` `evaluate.py`, `preflight.py`, `quasi_check.py`, `identity_audit.py`, `judge_identity.py`,
  `diff_outputs.py`, `watch_sources.py`, `fetch_sample.sh`
- `tests/` unit and service tests (+ `tests/js` parity harness, `tests/redteam` adversarial sweeps)
- `docs/` `index.html` (design), `pitch.html`, `deploy.md`, `failures.md`

## Commands
- install: `make install` (= `uv sync`)
- lint: `make lint`; tests: `make test` (run `make lint test` before every commit)
- sample run + eval: `make evaluate-sample` (uses `DATA_DIR=data/sample_run`, apart from real runs)
- full run + eval (needs `.env`): `make evaluate-full`
- service: `make run` (= `./run.sh`, port `$PORT`, default 9000) → `curl localhost:9000/v1/health`
- leak sweep before an evaluation: `make preflight` (`PREFLIGHT_OUT=`, `PREFLIGHT_RAW=`)
- docker: `make docker-build`, then `docker run -p 9000:9000 --env-file .env -v kipu-data:/data kipu-anonymizer`
- validator: `node validar.mjs <out_dir> --public <dir with record_index.json>`

## Config (`.env`, see `.env.example`)
`PSEUDONYM_SECRET` (required), `FUENTES_URL`, `FUENTES_TOKEN`, `DATA_DIR` (default `data`),
`ENTRYPOINT_TOKEN` (optional bearer auth), `PORT`; LLM layer: `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`,
`LLM_ENABLED`, `LLM_MAX_COST_USD`, `LLM_PRIMARY`, `LLM_SECOND`, `LLM_STRONG`, `LLM_CONCURRENCY`.

## Conventions
- Conventional Commits in English, imperative: `type(scope): summary` (feat, fix, test, docs,
  refactor, chore, ci). One logical change per commit; one "why" line in the body when not obvious.
- Stage explicit paths, never `git add -A`. Never push. No AI attribution anywhere.
- Commit only at green checkpoints, after `make lint test`.
- Type hints everywhere, small functions, docstrings only for non-obvious "why", no dead or
  commented-out code, no prints in library code (use `logging`).
- Docs (README.md, README.es.md, DECISIONS.md, docs/failures.md) change in the same commit as the
  code they describe.
- No corpus values in tracked files: tests and docs use invented values and reference records by id.

## Hard rules
- No PII libraries or services (Presidio, scrubadub, Comprehend PII, ...). Rules and our own code only.
- Replace, never rewrite: only detected spans change; the rest of each record stays byte-identical.
- The registry (`$DATA_DIR/registry.sqlite`) is never rebuilt: person ids and surrogates persist
  across runs so pseudonyms stay stable when sources grow.
- Never log or print tokens or secrets (`FUENTES_TOKEN`, `PSEUDONYM_SECRET`, `ENTRYPOINT_TOKEN`, API keys).
- Never modify `validar.mjs`, `api/`, `ENUNCIADO.md`.

When unsure, state the assumption in the summary and keep going.
